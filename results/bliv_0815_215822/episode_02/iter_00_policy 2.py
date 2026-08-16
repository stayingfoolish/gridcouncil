class Policy:
    def __init__(self):
        """Initializes the policy with strategic thresholds for battery management."""
        self.charge_threshold = 0.3
        self.discharge_threshold = 0.7
        self.price_margin = 0.05

    def take_action(self,
                    current_energy_stored_kwh: float,
                    current_pv_generation_kw: float,
                    current_demand_kw: float,
                    current_grid_buy_price: float,
                    current_grid_sell_price: float,
                    battery_capacity_kwh: float) -> tuple:
        """Determines optimal battery action to minimize energy costs."""
        
        soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
        net_power = current_pv_generation_kw - current_demand_kw
        
        action_kw = 0.0
        reason = ""
        
        if net_power > 0:
            if soc < self.discharge_threshold:
                max_charge = min(10, (battery_capacity_kwh - current_energy_stored_kwh) / 1.0)
                charge_amount = min(max_charge, net_power)
                if charge_amount > 0.1:
                    action_kw = charge_amount
                    reason = f"charging: excess PV {net_power:.2f}kW, SOC {soc:.1%}, price {current_grid_buy_price:.3f}€/kWh"
                else:
                    action_kw = 0.0
                    reason = f"no action: excess PV {net_power:.2f}kW but battery near full at SOC {soc:.1%}"
            else:
                action_kw = 0.0
                reason = f"no action: excess PV but battery full at SOC {soc:.1%}"
        
        else:
            deficit = abs(net_power)
            
            if soc > self.charge_threshold and current_grid_sell_price > current_grid_buy_price * (1 + self.price_margin):
                max_discharge = min(5, current_energy_stored_kwh / 1.0)
                discharge_amount = min(max_discharge, deficit * 1.1)
                if discharge_amount > 0.1:
                    action_kw = -discharge_amount
                    reason = f"discharging: favorable sell price {current_grid_sell_price:.3f}€ vs buy {current_grid_buy_price:.3f}€, SOC {soc:.1%}"
                else:
                    action_kw = 0.0
                    reason = f"no action: discharge amount {discharge_amount:.2f}kW insufficient, SOC {soc:.1%}"
            
            elif soc > 0.5 and current_grid_buy_price > 0.25:
                max_discharge = min(5, current_energy_stored_kwh / 1.0)
                discharge_amount = min(max_discharge, deficit)
                if discharge_amount > 0.1:
                    action_kw = -discharge_amount
                    reason = f"discharging: high grid price {current_grid_buy_price:.3f}€/kWh, SOC {soc:.1%}, deficit {deficit:.2f}kW"
                else:
                    action_kw = 0.0
                    reason = f"no action: insufficient stored energy {current_energy_stored_kwh:.2f}kWh"
            
            elif soc > self.charge_threshold and current_grid_buy_price < 0.12:
                action_kw = 0.0
                reason = f"no action: cheap grid price {current_grid_buy_price:.3f}€/kWh, preserve battery at SOC {soc:.1%}"
            
            else:
                action_kw = 0.0
                reason = f"no action: deficit {deficit:.2f}kW better from grid at {current_grid_buy_price:.3f}€/kWh, SOC {soc:.1%}"
        
        action_kw = max(-5, min(10, action_kw))
        
        return action_kw, reason
