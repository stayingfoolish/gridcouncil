class DispatchPolicy:
    def __init__(self):
        """Initializes the policy with price history tracking."""
        self.price_history = []

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
        """Decide this hour's flexible dispatch."""

        self.price_history.append(current_price)
        if len(self.price_history) > 24:
            self.price_history.pop(0)

        sorted_prices = sorted(self.price_history)
        n = len(sorted_prices)
        low_threshold = sorted_prices[max(0, n // 4)]
        high_threshold = sorted_prices[min(n - 1, 3 * n // 4)]
        mid_price = sum(sorted_prices) / n if n > 0 else current_price

        must_serve_backlog = oldest_backlog_age_h >= 23.0

        flex_serve_mw = 0.0

        if must_serve_backlog:
            flex_serve_mw = min(250.0, backlog_mwh)
        else:
            if current_price <= low_threshold:
                flex_serve_mw = arriving_flex_mw
                if backlog_mwh > 0:
                    flex_serve_mw += min(100.0, backlog_mwh * 0.25)
            elif current_price <= mid_price:
                flex_serve_mw = arriving_flex_mw * 0.5
                if backlog_mwh > 0 and oldest_backlog_age_h > 12:
                    flex_serve_mw += min(50.0, backlog_mwh * 0.1)
            else:
                if oldest_backlog_age_h > 18:
                    flex_serve_mw = min(100.0, backlog_mwh * 0.3)
                else:
                    flex_serve_mw = 0.0

        flex_serve_mw = min(250.0, max(0.0, flex_serve_mw))

        soc_ratio = battery_soc_mwh / max(1.0, battery_capacity_mwh)

        battery_mw = 0.0
        if current_price <= low_threshold and soc_ratio < 0.8:
            battery_mw = min(battery_power_mw, battery_capacity_mwh - battery_soc_mwh)
        elif current_price >= high_threshold and soc_ratio > 0.3:
            battery_mw = -min(battery_power_mw, battery_soc_mwh)

        return float(flex_serve_mw), float(battery_mw)
