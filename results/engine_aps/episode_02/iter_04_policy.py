import numpy as np
from collections import deque
from typing import Tuple

class DispatchPolicy:
    def __init__(self):
        self.price_history = deque(maxlen=72)
        self.hour_of_day_prices = {}
        self.regime_windows = []
        self.dynamic_target_soc = 50.0
        self.cycle_intensity = 1.0
        self.regret_threshold = 5.0
        self.valley_margin = 0.15
        self.peak_margin = 0.25
        self.price_volatility_window = 24
        self.backlog_queue = []
        self.last_24h_prices = deque(maxlen=24)
        self.step_count = 0

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
    ) -> Tuple[float, float]:

        self.price_history.append(current_price)
        self.last_24h_prices.append(current_price)
        self.step_count += 1

        if hour_of_day not in self.hour_of_day_prices:
            self.hour_of_day_prices[hour_of_day] = []
        self.hour_of_day_prices[hour_of_day].append(current_price)

        flex_serve_mw = self._compute_flex_scheduling(
            arriving_flex_mw, backlog_mwh, oldest_backlog_age_h, current_price
        )

        battery_mw = self._compute_battery_action(
            current_price, battery_soc_mwh, battery_capacity_mwh,
            battery_power_mw, hour_of_day
        )

        return flex_serve_mw, battery_mw

    def _get_price_stats(self):
        if len(self.price_history) == 0:
            return 50.0, 30.0, 100.0, 70.0

        prices = list(self.price_history)
        mean = np.mean(prices)
        std = np.std(prices) if len(prices) > 1 else 10.0
        min_price = np.min(prices)
        max_price = np.max(prices)
        return mean, std, min_price, max_price

    def _get_hour_forecast(self, hour_of_day: int):
        if hour_of_day not in self.hour_of_day_prices:
            return 50.0

        recent = self.hour_of_day_prices[hour_of_day][-7:]
        if len(recent) == 0:
            return 50.0

        historical_mean = np.mean(recent) if len(recent) > 0 else 50.0

        if len(self.last_24h_prices) >= 6:
            recent_avg = np.mean(list(self.last_24h_prices)[-6:])
        else:
            recent_avg = historical_mean

        if len(recent) >= 2:
            trend = (recent[-1] - recent[0]) / len(recent)
        else:
            trend = 0.0

        forecast = 0.6 * historical_mean + 0.3 * recent_avg + 0.1 * trend
        return max(20.0, forecast)

    def _detect_price_regime(self, current_price: float):
        if len(self.price_history) < 12:
            return "neutral"

        mean, std, min_price, max_price = self._get_price_stats()
        price_range = max_price - min_price

        if current_price < mean - 0.5 * std:
            return "valley"
        elif current_price > mean + 0.5 * std:
            return "peak"
        else:
            return "plateau"

    def _compute_flex_scheduling(self, arriving_flex_mw: float,
                                  backlog_mwh: float, oldest_backlog_age_h: float,
                                  current_price: float) -> float:

        if oldest_backlog_age_h >= 20.0:
            return arriving_flex_mw + backlog_mwh / 1.0

        if backlog_mwh <= 0:
            return arriving_flex_mw

        next_6h_prices = []
        for offset in range(1, 7):
            forecast_hour = (self.step_count + offset) % 24
            next_6h_prices.append(self._get_hour_forecast(forecast_hour))

        min_future_price = min(next_6h_prices) if next_6h_prices else current_price
        regret_cost = max(0, min_future_price - current_price)

        regime = self._detect_price_regime(current_price)

        if regime == "valley":
            flex_serve_mw = arriving_flex_mw
        elif regime == "peak":
            flex_serve_mw = min(arriving_flex_mw, max(0, arriving_flex_mw * 0.3))
        else:
            if regret_cost > self.regret_threshold:
                flex_serve_mw = arriving_flex_mw
            else:
                flex_serve_mw = arriving_flex_mw * 0.7

        backlog_capacity = min(backlog_mwh, 100.0)
        flex_serve_mw = min(flex_serve_mw + backlog_capacity * 0.1, arriving_flex_mw + backlog_capacity)

        return max(0, flex_serve_mw)

    def _compute_dynamic_target_soc(self, hour_of_day: int) -> float:
        next_6h_prices = []
        for offset in range(7):
            forecast_hour = (hour_of_day + offset) % 24
            next_6h_prices.append(self._get_hour_forecast(forecast_hour))

        min_future = min(next_6h_prices)
        max_future = max(next_6h_prices)
        future_range = max_future - min_future

        mean, std, _, _ = self._get_price_stats()

        if future_range > 60 and min(next_6h_prices[:3]) < mean - std:
            target_soc = 0.80 * 400.0
        elif future_range < 20:
            target_soc = 0.40 * 400.0
        else:
            target_soc = 0.50 * 400.0

        target_soc = np.clip(target_soc, 0.20 * 400.0, 0.85 * 400.0)
        return target_soc

    def _compute_battery_action(self, current_price: float, battery_soc_mwh: float,
                                 battery_capacity_mwh: float, battery_power_mw: float,
                                 hour_of_day: int) -> float:

        if len(self.price_history) < 6:
            return 0.0

        mean, std, min_price, max_price = self._get_price_stats()
        price_range = max_price - min_price

        if price_range < 20:
            self.cycle_intensity = 0.3
        elif price_range > 100:
            self.cycle_intensity = 1.0
        else:
            self.cycle_intensity = 0.3 + 0.7 * (price_range - 20) / 80.0

        target_soc = self._compute_dynamic_target_soc(hour_of_day)
        soc_error = target_soc - battery_soc_mwh

        valley_threshold = mean * (1 - self.valley_margin)
        peak_threshold = mean * (1 + self.peak_margin)

        if current_price < valley_threshold and soc_error > 0:
            charge_rate = min(battery_power_mw,
                            battery_power_mw * self.cycle_intensity * abs(soc_error) / target_soc)
            return charge_rate
        elif current_price > peak_threshold and soc_error < 0:
            discharge_rate = -min(battery_power_mw,
                                 battery_power_mw * self.cycle_intensity * abs(soc_error) / target_soc)
            return discharge_rate
        elif abs(soc_error) > target_soc * 0.1:
            correction_rate = battery_power_mw * 0.2 * (soc_error / target_soc)
            return np.clip(correction_rate, -battery_power_mw, battery_power_mw)
        else:
            return 0.0
