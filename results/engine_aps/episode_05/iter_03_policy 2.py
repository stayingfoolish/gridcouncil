class DispatchPolicy:
    def __init__(self):
        self.price_history = []
        self.max_history_length = 336  # 2 weeks for better patterns
        self.charge_soc_target = 0.85
        self.discharge_soc_min = 0.10

        # New: Price statistics
        self.hourly_patterns = {}  # hour -> (mean, p25, p75)
        self.recent_volatility = 0.0

        # New: Workload cost accounting
        self.deadline_cost_per_mwh = 5000  # $/MWh for overdue compute
        self.base_energy_cost = 50  # $/MWh baseline assumption

    def update_price_statistics(self, current_price, hour_of_day):
        """Build hour-of-day price distributions for pattern recognition."""
        self.price_history.append(current_price)
        if len(self.price_history) > self.max_history_length:
            self.price_history.pop(0)

        # Compute rolling volatility
        if len(self.price_history) >= 24:
            recent_24h = self.price_history[-24:]
            self.recent_volatility = (max(recent_24h) - min(recent_24h)) / (sum(recent_24h) / 24 + 1)

        # Update hour-of-day patterns
        if hour_of_day not in self.hourly_patterns:
            self.hourly_patterns[hour_of_day] = []
        self.hourly_patterns[hour_of_day].append(current_price)
        if len(self.hourly_patterns[hour_of_day]) > 52:  # Keep 1 year
            self.hourly_patterns[hour_of_day].pop(0)

    def get_price_context(self, hour_of_day, current_price):
        """Determine if current price is attractive or extreme."""
        if hour_of_day not in self.hourly_patterns or len(self.hourly_patterns[hour_of_day]) < 4:
            avg_price = sum(self.price_history) / len(self.price_history) if self.price_history else 50
            return current_price / avg_price

        prices_at_hour = self.hourly_patterns[hour_of_day]
        p25 = sorted(prices_at_hour)[len(prices_at_hour) // 4]
        p75 = sorted(prices_at_hour)[3 * len(prices_at_hour) // 4]
        median = sorted(prices_at_hour)[len(prices_at_hour) // 2]

        if current_price < p25:
            return 0.3  # Very cheap
        elif current_price < median:
            return 0.6  # Below median
        elif current_price < p75:
            return 0.9  # Above median
        else:
            return 1.2  # Expensive

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

        self.update_price_statistics(current_price, hour_of_day)
        price_context = self.get_price_context(hour_of_day, current_price)

        flex_serve_mw = 0.0
        battery_mw = 0.0

        # Priority 1: Manage deadline-critical backlog
        if oldest_backlog_age_h >= 18:  # Lowered from 20 to be more aggressive
            # Serve all backlog regardless of price/battery cost
            backlog_serve = min(backlog_mwh, battery_power_mw * 0.95)
            flex_serve_mw = arriving_flex_mw * 0.5 + backlog_serve
            battery_mw = -min(battery_power_mw * 0.95, battery_soc_mwh)
            return flex_serve_mw, battery_mw

        # Priority 2: Peak shaving with aggressive battery discharge
        if price_context >= 1.1 and battery_soc_mwh > battery_capacity_mwh * self.discharge_soc_min:
            # High price: serve aggressively from battery
            discharge_amount = min(
                battery_power_mw * 0.9,
                battery_soc_mwh - battery_capacity_mwh * self.discharge_soc_min
            )
            backlog_serve = min(backlog_mwh * 0.3, discharge_amount * 0.5)
            flex_serve_mw = arriving_flex_mw * 0.8 + backlog_serve
            battery_mw = -discharge_amount
            return flex_serve_mw, battery_mw

        # Priority 3: Opportunistic charging at cheap hours
        if price_context <= 0.5 and battery_soc_mwh < battery_capacity_mwh * self.charge_soc_target:
            # Very cheap: charge battery
            available_capacity = battery_capacity_mwh * self.charge_soc_target - battery_soc_mwh
            battery_mw = min(battery_power_mw * 0.85, available_capacity)
            flex_serve_mw = arriving_flex_mw * 0.4  # Reduced flex to prioritize charging
            return flex_serve_mw, battery_mw

        # Priority 4: Cost-aware flexible workload scheduling
        # Serve more flexible work when battery is charged AND either:
        # - prices are below median, or
        # - battery needs to cycle for health
        if battery_soc_mwh > battery_capacity_mwh * 0.5 and price_context <= 0.9:
            flex_serve_mw = arriving_flex_mw * 0.75
        elif battery_soc_mwh < battery_capacity_mwh * 0.3:
            # Battery low: defer flex work to encourage charging
            flex_serve_mw = arriving_flex_mw * 0.25
        else:
            flex_serve_mw = arriving_flex_mw * 0.5

        # Opportunistic charging in neutral price periods
        if battery_soc_mwh < battery_capacity_mwh * 0.7 and 0.5 <= price_context <= 0.9:
            available_capacity = battery_capacity_mwh * 0.7 - battery_soc_mwh
            if available_capacity > 0:
                battery_mw = min(battery_power_mw * 0.6, available_capacity)
                flex_serve_mw *= 0.8  # Slightly reduce flex to allow charging

        return flex_serve_mw, battery_mw
