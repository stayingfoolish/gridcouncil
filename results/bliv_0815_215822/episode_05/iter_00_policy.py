class Policy:
    def __init__(self):
        """Initializes the policy with strategic thresholds for battery management."""
        self.charge_price_threshold = 0.15
        self.discharge_price_threshold = 0.25
        self.battery_charge_threshold = 0.8
        self.battery_discharge_threshold = 0.2
        self.max_charge_power = 10.0
        self.max_discharge_power = 5.0
        self.pv_reserve_margin = 0.1
        
    def take_action(self,
        current_energy_stored_kwh: float,
        current_pv_generation_kw: float,
        current_demand_kw: float,
        current_grid_buy_price: float,
        current_grid_sell_price: float,
        battery_capacity_kwh: float,
    ) -> tuple:
        """Determines optimal battery action based on price signals and battery state."""
        
        action_kw = 0.0
        reason = ""
        
        battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
        net_generation = current_pv_generation_kw - current_demand_kw
        price_spread = current_grid_sell_price - current_grid_buy_price
        
        if net_generation > self.pv_reserve_margin:
            excess_power = net_generation - self.pv_reserve_margin
            
            if battery_soc < self.battery_charge_threshold:
                charge_rate = min(excess_power, self.max_charge_power)
                space_available = (battery_capacity_kwh - current_energy_stored_kwh) / battery_capacity_kwh
                
                if charge_rate > 0.05 and space_available > 0.1:
                    action_kw = charge_rate
                    reason = f"charge excess PV at {charge_rate:.2f}kW, SOC={battery_soc:.1%}, excess={excess_power:.2f}kW"
                    return action_kw, reason
            
            if current_grid_sell_price > 0.05 and excess_power > 0.5:
                action_kw = 0.0
                reason = f"sell excess PV to grid at {current_grid_sell_price:.3f}€/kWh, excess={excess_power:.2f}kW"
                return action_kw, reason
        
        elif net_generation < -self.pv_reserve_margin:
            deficit = abs(net_generation) + self.pv_reserve_margin
            
            if battery_soc > self.battery_discharge_threshold:
                discharge_rate = min(deficit, self.max_discharge_power)
                
                if current_grid_buy_price > self.discharge_price_threshold:
                    discharge_rate = min(discharge_rate, self.max_discharge_power * 0.8)
                    action_kw = -discharge_rate
                    reason = f"discharge at {discharge_rate:.2f}kW to avoid expensive grid ({current_grid_buy_price:.3f}€/kWh), SOC={battery_soc:.1%}"
                    return action_kw, reason
                else:
                    action_kw = -discharge_rate
                    reason = f"discharge at {discharge_rate:.2f}kW, SOC={battery_soc:.1%}, grid price={current_grid_buy_price:.3f}€/kWh"
                    return action_kw, reason
            
            if current_grid_buy_price > self.discharge_price_threshold and battery_soc > self.battery_discharge_threshold + 0.1:
                discharge_rate = min(deficit * 0.5, self.max_discharge_power)
                action_kw = -discharge_rate
                reason = f"partial discharge at {discharge_rate:.2f}kW, expensive grid {current_grid_buy_price:.3f}€/kWh > {self.discharge_price_threshold:.3f}€/kWh threshold"
                return action_kw, reason
        
        if current_grid_buy_price < self.charge_price_threshold and battery_soc < self.battery_charge_threshold:
            available_space = (battery_capacity_kwh - current_energy_stored_kwh) / battery_capacity_kwh
            
            if available_space > 0.15:
                charge_rate = min(self.max_charge_power * 0.7, self.max_charge_power)
                action_kw = charge_rate
                reason = f"charge at cheap price {current_grid_buy_price:.3f}€/kWh < {self.charge_price_threshold:.3f}€/kWh, SOC={battery_soc:.1%}"
                return action_kw, reason
        
        if battery_soc > 0.95:
            if current_grid_sell_price > current_grid_buy_price * 1.5 and net_generation > 0:
                action_kw = 0.0
                reason = f"battery full (SOC={battery_soc:.1%}), sell excess to grid at {current_grid_sell_price:.3f}€/kWh"
                return action_kw, reason
            
            if net_generation < 0:
                deficit = abs(net_generation)
                discharge_rate = min(deficit, self.max_discharge_power * 0.5)
                action_kw = -discharge_rate
                reason = f"battery full (SOC={battery_soc:.1%}), discharge to prevent overflow"
                return action_kw, reason
        
        if battery_soc < 0.05 and current_demand_kw > current_pv_generation_kw:
            action_kw = 0.0
            reason = f"battery critical (SOC={battery_soc:.1%}), buy from grid to meet demand"
            return action_kw, reason
        
        action_kw = 0.0
        reason = f"hold: SOC={battery_soc:.1%}, net_gen={net_generation:.2f}kW, buy_price={current_grid_buy_price:.3f}€/kWh, sell_price={current_grid_sell_price:.3f}€/kWh"
        
        return action_kw, reason
