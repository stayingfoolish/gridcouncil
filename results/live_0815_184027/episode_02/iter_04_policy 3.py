class DispatchPolicy:
    def __init__(self):
        """Initializes the policy with price history and decision thresholds."""
        self.price_history = []
        self.max_history = 168  # One week of hourly prices
        
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
        
        self.price_history.append(current_price)
        if len(self.price_history) > self.max_history:
            self.price_history.pop(0)
        
        # Calculate price percentiles and volatility
        if len(self.price_history) >= 24:
            sorted_prices = sorted(self.price_history)
            idx_10 = max(0, len(sorted_prices) // 10)
            idx_25 = max(0, len(sorted_prices) // 4)
            idx_50 = len(sorted_prices) // 2
            idx_75 = (3 * len(sorted_prices)) // 4
            idx_90 = max(0, (9 * len(sorted_prices)) // 10)
            
            p10 = sorted_prices[idx_10]
            p25 = sorted_prices[idx_25]
            p50 = sorted_prices[idx_50]
            p75 = sorted_prices[idx_75]
            p90 = sorted_prices[idx_90]
            
            price_spread = p90 - p10
            base_spread = 20.0
            volatility_factor = 0.5 + min(1.0, price_spread / base_spread) * 1.0
        else:
            p10 = p25 = p50 = p75 = p90 = current_price
            volatility_factor = 0.75
        
        # Dynamic charge target based on volatility
        target_soc_ratio = 0.60 + (volatility_factor - 0.5) * 0.5
        battery_target_mwh = battery_capacity_mwh * target_soc_ratio
        battery_deficit = battery_target_mwh - battery_soc_mwh
        
        # Price classification
        is_cheap_hour = current_price < p50 * 0.85
        is_expensive_hour = current_price > p75
        is_elevated_price = current_price > p50 * 1.1
        is_extreme_price = current_price > p90 * 1.15
        
        # Urgency factor for backlog deadline
        urgency_factor = 1.0
        if oldest_backlog_age_h > 20:
            urgency_factor = 2.0
        elif oldest_backlog_age_h > 16:
            urgency_factor = 1.5
        elif oldest_backlog_age_h > 12:
            urgency_factor = 1.2
        
        # Flex serve decision
        flex_serve_mw = 0.0
        
        if backlog_mwh > 1e-6:
            # Must serve backlog if approaching deadline
            max_backlog_serve = min(arriving_flex_mw + backlog_mwh / 1.0,
                                   battery_capacity_mwh)
            
            if oldest_backlog_age_h > 20:
                flex_serve_mw = max_backlog_serve
            elif is_cheap_hour or is_extreme_price:
                backlog_serve = min(backlog_mwh / 1.0,
                                   arriving_flex_mw * 1.5 * urgency_factor)
                flex_serve_mw = arriving_flex_mw + backlog_serve
            elif is_expensive_hour:
                flex_serve_mw = max(arriving_flex_mw * 0.2,
                                   backlog_mwh / max(1.0, 24.0 - oldest_backlog_age_h))
            else:
                flex_serve_mw = arriving_flex_mw * 0.5
            
            flex_serve_mw = min(flex_serve_mw, arriving_flex_mw + backlog_mwh / 1.0)
        else:
            # No backlog: price-based dispatch
            if is_cheap_hour:
                flex_serve_mw = arriving_flex_mw * 1.2
            elif is_expensive_hour:
                flex_serve_mw = arriving_flex_mw * 0.3
            else:
                flex_serve_mw = arriving_flex_mw * 0.6
        
        flex_serve_mw = max(0.0, flex_serve_mw)
        
        # Battery dispatch
        battery_mw = 0.0
        
        if is_cheap_hour and battery_deficit > 1e-6:
            available_charge_power = min(battery_power_mw,
                                        battery_deficit / 1.0,
                                        (battery_capacity_mwh - battery_soc_mwh) / 1.0)
            battery_mw = max(0.0, available_charge_power)
        elif is_elevated_price and battery_soc_mwh > battery_capacity_mwh * 0.10:
            available_discharge_power = min(battery_power_mw,
                                           (battery_soc_mwh - battery_capacity_mwh * 0.10) / 1.0)
            battery_mw = -max(0.0, available_discharge_power)
        elif is_extreme_price and battery_soc_mwh > battery_capacity_mwh * 0.05:
            available_discharge_power = min(battery_power_mw,
                                           (battery_soc_mwh - battery_capacity_mwh * 0.05) / 1.0)
            battery_mw = -max(0.0, available_discharge_power)
        elif current_price < p25 and battery_deficit > 1e-6:
            available_charge_power = min(battery_power_mw,
                                        battery_deficit / 1.0,
                                        (battery_capacity_mwh - battery_soc_mwh) / 1.0)
            battery_mw = max(0.0, available_charge_power)
        
        return flex_serve_mw, battery_mw
