class DispatchPolicy:
    def __init__(self):
        """Initializes the policy with price history and state tracking."""
        self.price_history = []
        self.hour_counter = 0
        self.price_percentile_window = 24
        self.battery_efficiency = 0.88
        self.min_battery_buffer = 0.15
        
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
        """Dispatch deferrable compute and battery optimally."""
        
        self.price_history.append(current_price)
        if len(self.price_history) > self.price_percentile_window:
            self.price_history.pop(0)
        self.hour_counter += 1
        
        price_percentile_25 = sorted(self.price_history)[len(self.price_history) // 4] if self.price_history else current_price
        price_percentile_75 = sorted(self.price_history)[3 * len(self.price_history) // 4] if self.price_history else current_price
        price_median = sorted(self.price_history)[len(self.price_history) // 2] if self.price_history else current_price
        
        backlog_hours_remaining = 24 - oldest_backlog_age_h if backlog_mwh > 0 else float('inf')
        backlog_capacity_mw = backlog_mwh / max(backlog_hours_remaining, 1) if backlog_mwh > 0 else 0
        
        flex_serve_mw = 0.0
        battery_mw = 0.0
        
        min_battery_soc = battery_capacity_mwh * self.min_battery_buffer
        max_battery_soc = battery_capacity_mwh * 0.95
        usable_capacity = max_battery_soc - min_battery_soc
        
        can_discharge = battery_soc_mwh > min_battery_soc
        can_charge = battery_soc_mwh < max_battery_soc
        
        is_expensive = current_price > price_percentile_75
        is_cheap = current_price < price_percentile_25
        is_normal = price_percentile_25 <= current_price <= price_percentile_75
        
        urgency_factor = 0.0
        if backlog_mwh > 0:
            urgency_factor = max(0.0, (oldest_backlog_age_h - 6) / 18.0)
        
        flex_serve_mw = 0.0
        if is_cheap or is_normal:
            flex_serve_mw = arriving_flex_mw
        elif backlog_mwh > 0 and backlog_hours_remaining < 4:
            flex_serve_mw = arriving_flex_mw + min(backlog_capacity_mw, backlog_mwh / max(backlog_hours_remaining, 1))
        elif backlog_mwh > 0 and urgency_factor > 0.5:
            flex_serve_mw = arriving_flex_mw + backlog_capacity_mw * urgency_factor
        
        flex_serve_mw = max(0.0, flex_serve_mw)
        
        if is_cheap and can_charge:
            charge_capacity = (max_battery_soc - battery_soc_mwh) / 1.0
            battery_mw = min(battery_power_mw, charge_capacity)
        elif is_expensive:
            if can_discharge and current_price > price_median * 1.3:
                discharge_capacity = (battery_soc_mwh - min_battery_soc) / 1.0
                battery_mw = -min(battery_power_mw, discharge_capacity)
        elif is_normal:
            if battery_soc_mwh > max_battery_soc * 0.85 and can_discharge:
                battery_mw = -min(battery_power_mw * 0.3, (battery_soc_mwh - max_battery_soc * 0.75) / 1.0)
            elif battery_soc_mwh < min_battery_soc * 2.0 and can_charge and current_price < price_median:
                charge_amount = min_battery_soc * 1.5 - battery_soc_mwh
                battery_mw = min(battery_power_mw * 0.4, charge_amount / 1.0)
        
        total_load = firm_load_mw + flex_serve_mw
        if total_load + battery_mw > 750 and is_expensive:
            battery_discharge_reduction = min(-battery_mw if battery_mw < 0 else 0, 
                                             (total_load + battery_mw - 750) / 2.0)
            battery_mw += battery_discharge_reduction
            
            excess_flex = max(0.0, total_load - 500)
            if excess_flex > 0:
                deferrable_reduction = min(flex_serve_mw * 0.3, excess_flex)
                flex_serve_mw -= deferrable_reduction
        
        battery_mw = max(-battery_power_mw, min(battery_power_mw, battery_mw))
        flex_serve_mw = max(0.0, flex_serve_mw)
        
        return flex_serve_mw, battery_mw
