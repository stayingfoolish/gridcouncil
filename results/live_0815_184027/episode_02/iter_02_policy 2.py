class DispatchPolicy:
    def __init__(self):
        """Initializes the policy with price history and configuration."""
        self.price_history = []
        self.max_price_history = 48
        self.battery_target_soc_fraction = 0.50
        self.price_trend = 0.0
        self.price_percentile_10 = 30.0
        self.price_percentile_90 = 80.0

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
        """Decide this hour's flexible dispatch based on price trends and backlog urgency."""

        self.price_history.append(current_price)
        if len(self.price_history) > self.max_price_history:
            self.price_history.pop(0)

        if len(self.price_history) >= 2:
            recent_prices = self.price_history[-4:] if len(self.price_history) >= 4 else self.price_history
            price_changes = [recent_prices[i+1] - recent_prices[i] for i in range(len(recent_prices)-1)]
            self.price_trend = sum(price_changes) / len(price_changes) if price_changes else 0.0
        else:
            self.price_trend = 0.0

        if len(self.price_history) >= 10:
            sorted_prices = sorted(self.price_history)
            self.price_percentile_10 = sorted_prices[len(sorted_prices) // 10]
            self.price_percentile_90 = sorted_prices[9 * len(sorted_prices) // 10]

        is_cheap_hour = current_price <= self.price_percentile_10
        is_expensive_hour = current_price >= self.price_percentile_90

        if self.price_trend > 0.10:
            dynamic_target_fraction = 0.70
        elif self.price_trend > 0.03:
            dynamic_target_fraction = 0.60
        elif self.price_trend < -0.10:
            dynamic_target_fraction = 0.30
        elif self.price_trend < -0.03:
            dynamic_target_fraction = 0.40
        else:
            dynamic_target_fraction = 0.50

        target_battery_soc = battery_capacity_mwh * dynamic_target_fraction
        battery_deficit = target_battery_soc - battery_soc_mwh
        battery_excess = battery_soc_mwh - target_battery_soc

        backlog_urgency = 0.0
        if backlog_mwh > 1e-6:
            backlog_urgency = max(0.0, (oldest_backlog_age_h - 12.0) / 12.0)

        urgency_threshold = 0.5
        if backlog_urgency > urgency_threshold and backlog_mwh > 1e-6:
            flex_serve_mw = min(arriving_flex_mw + backlog_mwh / 1.0, 250.0)
        elif is_cheap_hour or (is_expensive_hour and battery_soc_mwh < battery_capacity_mwh * 0.3):
            flex_serve_mw = arriving_flex_mw
        elif is_expensive_hour:
            flex_serve_mw = max(0.0, arriving_flex_mw * 0.3)
        else:
            flex_serve_mw = arriving_flex_mw

        if is_cheap_hour and battery_deficit > 1e-6:
            available_charge_power = min(battery_power_mw, battery_deficit / 1.0, (battery_capacity_mwh - battery_soc_mwh) / 1.0)
            battery_mw = available_charge_power
        elif is_expensive_hour and battery_excess > 1e-6:
            available_discharge_power = min(battery_power_mw, battery_excess / 1.0, battery_soc_mwh / 1.0)
            battery_mw = -available_discharge_power
        elif battery_deficit > 20.0 and not is_expensive_hour:
            available_charge_power = min(battery_power_mw * 0.5, battery_deficit / 1.0, (battery_capacity_mwh - battery_soc_mwh) / 1.0)
            battery_mw = available_charge_power
        elif battery_excess > 20.0 and current_price > self.price_percentile_10 * 1.2:
            available_discharge_power = min(battery_power_mw * 0.3, battery_excess / 1.0, battery_soc_mwh / 1.0)
            battery_mw = -available_discharge_power
        else:
            battery_mw = 0.0

        return flex_serve_mw, battery_mw
