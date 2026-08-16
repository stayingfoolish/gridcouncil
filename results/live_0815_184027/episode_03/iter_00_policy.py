class DispatchPolicy:
    def __init__(self):
        """Initializes the policy with state tracking and learned thresholds."""
        self.price_history = []
        self.hour_history = []
        self.price_percentile_25 = 30.0
        self.price_percentile_75 = 75.0
        self.adaptive_alpha = 0.15
        self.flexibility_buffer = 2.0
        
    def take_action(self,
                    hour_of_day: int,
                    current_price: float,
                    firm_load_mw: float,
                    arriving_flex_mw: float,
                    backlog_mwh: float,
                    oldest_backlog_age_h: float,
                    battery_soc_mwh: float,
                    battery_capacity_mwh: float,
                    battery_power_mw: float) -> tuple:
        """Dispatch policy balancing price, backlog age, and battery state."""
        
        self.price_history.append(current_price)
        self.hour_history.append(hour_of_day)
        if len(self.price_history) > 168:
            self.price_history.pop(0)
            self.hour_history.pop(0)
        
        if len(self.price_history) >= 24:
            sorted_prices = sorted(self.price_history)
            self.price_percentile_25 = sorted_prices[len(sorted_prices) // 4]
            self.price_percentile_75 = sorted_prices[3 * len(sorted_prices) // 4]
        
        urgency_factor = max(0.0, (oldest_backlog_age_h - 12.0) / 12.0)
        time_until_deadline = max(0.0, 24.0 - oldest_backlog_age_h)
        
        battery_soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0.5
        
        price_ratio = current_price / max(self.price_percentile_75, 1.0)
        
        flex_serve_mw = 0.0
        battery_mw = 0.0
        
        if backlog_mwh > 0 and time_until_deadline < 2.0:
            flex_serve_mw = min(arriving_flex_mw + backlog_mwh, 250.0)
        elif current_price <= self.price_percentile_25:
            flex_serve_mw = arriving_flex_mw
            buffer_target = 0.85
            if battery_soc_ratio < buffer_target and arriving_flex_mw < 50:
                charge_capacity = min(battery_power_mw, 
                                     (battery_capacity_mwh - battery_soc_mwh) / 1.0)
                battery_mw = min(charge_capacity, 40.0)
        elif self.price_percentile_25 < current_price <= self.price_percentile_75:
            urgency_threshold = 0.3
            if urgency_factor > urgency_threshold:
                flex_serve_mw = arriving_flex_mw + min(backlog_mwh / max(time_until_deadline, 1.0), 50.0)
            else:
                flex_serve_mw = arriving_flex_mw * 0.5
            
            if battery_soc_ratio > 0.6:
                discharge_capacity = min(battery_power_mw, battery_soc_mwh / 1.0)
                battery_mw = -min(discharge_capacity, 30.0)
        else:
            if urgency_factor > 0.5:
                flex_serve_mw = arriving_flex_mw + min(backlog_mwh / max(time_until_deadline, 1.0), 100.0)
            elif backlog_mwh > 0:
                flex_serve_mw = arriving_flex_mw * 0.1
            
            if battery_soc_ratio > 0.5 and backlog_mwh > 0:
                discharge_capacity = min(battery_power_mw, battery_soc_mwh / 1.0)
                battery_mw = -min(discharge_capacity, 80.0)
        
        backlog_served_this_hour = flex_serve_mw
        flex_serve_mw = min(flex_serve_mw, arriving_flex_mw + backlog_mwh)
        flex_serve_mw = max(0.0, flex_serve_mw)
        
        if oldest_backlog_age_h < 6.0:
            min_charge_target = 0.2
        elif oldest_backlog_age_h < 12.0:
            min_charge_target = 0.4
        else:
            min_charge_target = 0.6
        
        if battery_soc_ratio < min_charge_target and battery_mw > 0:
            available_charge = min(battery_power_mw,
                                  (battery_capacity_mwh - battery_soc_mwh) / 1.0)
            battery_mw = min(available_charge, 50.0)
        elif battery_soc_ratio > 0.95:
            battery_mw = 0.0
        
        if battery_mw < 0:
            max_discharge = min(battery_power_mw, battery_soc_mwh / 1.0)
            battery_mw = max(battery_mw, -max_discharge)
        else:
            max_charge = min(battery_power_mw,
                            (battery_capacity_mwh - battery_soc_mwh) / 1.0)
            battery_mw = min(battery_mw, max_charge)
        
        flex_serve_mw = min(flex_serve_mw, 250.0)
        flex_serve_mw = max(0.0, flex_serve_mw)
        
        return (flex_serve_mw, battery_mw)
