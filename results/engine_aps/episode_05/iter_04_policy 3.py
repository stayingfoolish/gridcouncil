import collections
import numpy as np
from scipy import stats


class ARModel:
    def __init__(self, coefficients):
        self.coefficients = coefficients

    def forecast(self, price_history, steps=24):
        prices = np.array(price_history, dtype=float)
        forecast = []

        for _ in range(steps):
            if len(prices) >= len(self.coefficients):
                recent = prices[-len(self.coefficients):]
                next_price = np.dot(self.coefficients, recent)
            else:
                next_price = prices[-1]

            forecast.append(max(0, next_price))
            prices = np.append(prices, next_price)

        return np.array(forecast)


class DispatchPolicy:
    def __init__(self):
        self.price_history = collections.deque(maxlen=240)
        self.ar_model = None
        self.last_forecast_update = -5

        self.charge_soc_target = 0.75
        self.discharge_soc_min = 0.05

        self.forecast_price_next_24h = None
        self.historical_p50_by_hour = {}
        self.mpc_lookahead_hours = 24

    def _fit_ar24_robust(self, prices):
        if len(prices) < 48:
            return None

        prices = np.array(prices, dtype=float)
        n = len(prices)

        X = []
        y = []
        for t in range(24, n):
            X.append(prices[t-24:t])
            y.append(prices[t])

        X = np.array(X)
        y = np.array(y)

        try:
            coeffs, residuals, rank, s = np.linalg.lstsq(X, y, rcond=None)
            return ARModel(coeffs)
        except:
            return None

    def fit_ar_model(self):
        if len(self.price_history) < 48:
            return False
        prices = list(self.price_history)
        self.ar_model = self._fit_ar24_robust(prices)
        return self.ar_model is not None

    def forecast_24h_prices(self, current_price, hour_of_day):
        if len(self.price_history) < 48:
            return [current_price * (1.0 + 0.005 * (i % 24)) for i in range(24)]

        if self.ar_model is None:
            self.fit_ar_model()

        if self.ar_model is None:
            return [current_price * (1.0 + 0.005 * (i % 24)) for i in range(24)]

        forecast = self.ar_model.forecast(list(self.price_history), steps=24)
        median = np.median(list(self.price_history))
        forecast = np.clip(forecast, 0.5 * median, 3.0 * median)
        return list(forecast)

    def solve_mpc_dispatch(self, state_dict, price_forecast):
        lookahead_steps = 8

        actions = []
        soc = state_dict['battery_soc_mwh']
        backlog_age = state_dict['oldest_backlog_age_h']
        backlog = state_dict['backlog_mwh']
        arriving_flex = state_dict['arriving_flex_mw']
        battery_power_limit = state_dict['battery_power_mw']
        battery_capacity = state_dict['battery_capacity_mwh']

        for step in range(lookahead_steps):
            hour_idx = min(step * 3, 23)
            price_at_step = price_forecast[hour_idx]

            p50_now = self.historical_p50_by_hour.get(hour_idx, 50.0)

            if isinstance(p50_now, list) and len(p50_now) > 0:
                p50_now = np.percentile(p50_now, 50)
            else:
                p50_now = 50.0

            if backlog_age > 20:
                flex_serve = min(battery_power_limit, backlog)
                battery_discharge = min(battery_power_limit, max(0, soc - 0.05 * battery_capacity))
                battery_mw = -battery_discharge
            elif price_at_step > 1.3 * p50_now and soc > 0.5 * battery_capacity:
                flex_serve = arriving_flex * 0.7
                battery_mw = -battery_power_limit * 0.8
                soc = max(0, soc - battery_power_limit * 0.8)
            elif price_at_step < 0.6 * p50_now and soc < 0.75 * battery_capacity:
                flex_serve = arriving_flex * 0.3
                battery_mw = battery_power_limit * 0.8
                soc = min(battery_capacity, soc + battery_power_limit * 0.8)
            else:
                flex_serve = arriving_flex * 0.5
                battery_mw = 0.0

            flex_serve = max(0, min(flex_serve, backlog + arriving_flex))
            actions.append((flex_serve, battery_mw))
            backlog = max(0, backlog - flex_serve + arriving_flex)
            backlog_age += 3

        return actions[0]

    def take_action(self, hour_of_day, current_price, firm_load_mw, arriving_flex_mw,
                    backlog_mwh, oldest_backlog_age_h, battery_soc_mwh, battery_capacity_mwh,
                    battery_power_mw):

        self.price_history.append(current_price)

        if (hour_of_day - self.last_forecast_update) % 4 == 0:
            self.forecast_price_next_24h = self.forecast_24h_prices(current_price, hour_of_day)
            self.last_forecast_update = hour_of_day

        if hour_of_day not in self.historical_p50_by_hour:
            self.historical_p50_by_hour[hour_of_day] = []
        self.historical_p50_by_hour[hour_of_day].append(current_price)
        if len(self.historical_p50_by_hour[hour_of_day]) > 52:
            self.historical_p50_by_hour[hour_of_day].pop(0)

        if self.forecast_price_next_24h is None:
            self.forecast_price_next_24h = self.forecast_24h_prices(current_price, hour_of_day)

        state = {
            'firm_load_mw': firm_load_mw,
            'arriving_flex_mw': arriving_flex_mw,
            'backlog_mwh': backlog_mwh,
            'oldest_backlog_age_h': oldest_backlog_age_h,
            'battery_soc_mwh': battery_soc_mwh,
            'battery_capacity_mwh': battery_capacity_mwh,
            'battery_power_mw': battery_power_mw,
        }

        flex_serve, battery_mw = self.solve_mpc_dispatch(state, self.forecast_price_next_24h)

        flex_serve = max(0.0, flex_serve)
        battery_mw = max(-battery_power_mw, min(battery_power_mw, battery_mw))

        return flex_serve, battery_mw
