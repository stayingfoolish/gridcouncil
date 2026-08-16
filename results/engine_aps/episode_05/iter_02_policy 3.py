class DispatchPolicy:
    def __init__(self):
        self.price_history = []
        self.max_history_length = 168
        self.charge_target_soc = 0.8
        self.discharge_threshold = 1.3
        self.charge_threshold = 0.75
        self.critical_backlog_age = 20
        self.phase_2_start = 12
        self.phase_3_start = 20

    def calculate_hourly_phase(self, hour_of_day):
        if hour_of_day < self.phase_2_start:
            return 1
        elif hour_of_day < self.phase_3_start:
            return 2
        else:
            return 3

    def estimate_price_percentile(self, current_price):
        if len(self.price_history) < 10:
            return 0.5
        sorted_prices = sorted(self.price_history)
        return sum(1 for p in sorted_prices if p < current_price) / len(self.price_history)

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
        if len(self.price_history) > self.max_history_length:
            self.price_history.pop(0)

        avg_price = sum(self.price_history) / len(self.price_history) if self.price_history else 50.0
        phase = self.calculate_hourly_phase(hour_of_day)
        percentile = self.estimate_price_percentile(current_price)

        flex_serve_mw = 0.0
        battery_mw = 0.0

        if phase == 1:
            flex_serve_mw = arriving_flex_mw * 0.3
            if current_price < avg_price * self.charge_threshold:
                available_capacity = battery_capacity_mwh - battery_soc_mwh
                battery_mw = min(battery_power_mw, available_capacity)

        elif phase == 2:
            if current_price > avg_price * self.discharge_threshold and battery_soc_mwh > 0:
                backlog_serve = min(backlog_mwh, battery_power_mw * 0.9)
                flex_serve_mw = arriving_flex_mw * 0.2 + backlog_serve
                battery_mw = -min(battery_power_mw * 0.9, battery_soc_mwh)
            else:
                flex_serve_mw = arriving_flex_mw * 0.4

        else:
            if oldest_backlog_age_h >= self.critical_backlog_age:
                flex_serve_mw = arriving_flex_mw + min(backlog_mwh, battery_power_mw)
            else:
                flex_serve_mw = arriving_flex_mw * 0.6

        return flex_serve_mw, battery_mw
