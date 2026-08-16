import math
from collections import deque

class DispatchPolicy:
    def __init__(self):
        self.price_history = deque(maxlen=168)
        self.price_percentiles = {}
        self.hour_count = 0
        self.price_threshold_multiplier = 1.2
        self.battery_target_soc_fraction = 0.5
        self.urgency_threshold = 12.0

    def take_action(self,
        hour_of_day: int,
        current_price: float,
        firm_load_mw: float,
        arriving_flex_mw: float,
        backlog_mwh: float,
        oldest_backlog_age_h: float,
        battery_soc_mwh: float,
        battery_capacity_mwh: float,
        battery_power_mw: float,
    ) -> tuple:

        self.price_history.append(current_price)
        self.hour_count += 1

        if len(self.price_history) >= 24:
            sorted_prices = sorted(self.price_history)
            self.price_percentiles = {
                'p25': sorted_prices[len(sorted_prices) // 4],
                'p50': sorted_prices[len(sorted_prices) // 2],
                'p75': sorted_prices[3 * len(sorted_prices) // 4],
                'p90': sorted_prices[int(0.9 * len(sorted_prices))],
                'min': sorted_prices[0],
                'max': sorted_prices[-1],
            }
        else:
            self.price_percentiles = {
                'p25': current_price,
                'p50': current_price,
                'p75': current_price,
                'p90': current_price,
                'min': current_price,
                'max': current_price,
            }

        target_battery_soc = battery_capacity_mwh * self.battery_target_soc_fraction
        battery_deficit = target_battery_soc - battery_soc_mwh
        battery_excess = battery_soc_mwh - target_battery_soc

        backlog_hours_remaining = max(0, 24 - oldest_backlog_age_h) if backlog_mwh > 0 else 24
        is_backlog_urgent = backlog_mwh > 0 and oldest_backlog_age_h > self.urgency_threshold

        typical_price = self.price_percentiles['p50']
        expensive_threshold = typical_price * self.price_threshold_multiplier

        is_cheap = current_price < self.price_percentiles['p25']
        is_expensive = current_price > expensive_threshold
        is_very_expensive = current_price > self.price_percentiles['p90']

        flex_serve_mw = 0.0
        battery_mw = 0.0

        if is_cheap:
            if battery_deficit > 0:
                charge_amount = min(battery_power_mw, battery_deficit / 1.0)
                battery_mw = charge_amount
            else:
                flex_serve_mw = arriving_flex_mw

        elif is_very_expensive:
            flex_serve_mw = 0.0

            if battery_soc_mwh > target_battery_soc * 0.3:
                discharge_amount = min(battery_power_mw, battery_excess / 1.0) if battery_excess > 0 else battery_power_mw * 0.5
                battery_mw = -min(discharge_amount, battery_power_mw)

        elif is_expensive:
            flex_serve_mw = 0.0

            if battery_soc_mwh > target_battery_soc * 0.4 and battery_excess > 0:
                discharge_amount = min(battery_power_mw, battery_excess / 1.0)
                battery_mw = -discharge_amount

        else:
            typical_load_contribution = min(arriving_flex_mw, 150.0)
            flex_serve_mw = typical_load_contribution

            if battery_deficit > 0:
                charge_amount = min(battery_power_mw * 0.5, battery_deficit / 2.0)
                battery_mw = charge_amount

        if is_backlog_urgent and backlog_hours_remaining < 6:
            max_serve = arriving_flex_mw + min(battery_power_mw, backlog_mwh)
            flex_serve_mw = max(flex_serve_mw, backlog_mwh / backlog_hours_remaining)
            flex_serve_mw = min(flex_serve_mw, max_serve)
            battery_mw = 0.0

        elif backlog_mwh > 0 and backlog_hours_remaining < 12:
            urgency_factor = 1.0 - (backlog_hours_remaining / 12.0)
            additional_flex = arriving_flex_mw * urgency_factor * 0.5
            flex_serve_mw = max(flex_serve_mw, additional_flex)

        battery_mw = max(-battery_power_mw, min(battery_mw, battery_power_mw))

        max_discharge = min(battery_power_mw, battery_soc_mwh / 1.0)
        battery_mw = max(-max_discharge, battery_mw)

        max_charge = min(battery_power_mw, (battery_capacity_mwh - battery_soc_mwh) / 1.0)
        battery_mw = min(max_charge, battery_mw)

        flex_serve_mw = max(0.0, flex_serve_mw)

        return flex_serve_mw, battery_mw
