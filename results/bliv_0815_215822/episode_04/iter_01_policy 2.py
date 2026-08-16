class Policy:
  def __init__(self):
    """Initializes the policy with efficiency parameters."""
    self.charge_efficiency = 0.92
    self.discharge_efficiency = 0.92
    self.price_ratio_threshold = 1.35

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> tuple:
    """Determines the target battery action based on current state."""
    
    energy_stored_ratio = current_energy_stored_kwh / battery_capacity_kwh
    pv_excess = max(0, current_pv_generation_kw - current_demand_kw)
    
    net_grid_demand = current_demand_kw - current_pv_generation_kw
    
    if pv_excess > 0.5 and energy_stored_ratio < 0.85:
      space_available = (battery_capacity_kwh * 0.85 - current_energy_stored_kwh) / self.charge_efficiency
      charge_amount = min(pv_excess, space_available, 10.0)
      if charge_amount > 0.3:
        reason = f"charge excess PV: {pv_excess:.2f}kW available, battery at {energy_stored_ratio*100:.1f}%"
        return charge_amount, reason
    
    if (current_grid_buy_price < current_grid_sell_price * 0.65 and 
        energy_stored_ratio < 0.75):
      space_available = (battery_capacity_kwh * 0.75 - current_energy_stored_kwh) / self.charge_efficiency
      max_charge = min(space_available, 8.0)
      
      if max_charge > 0.8:
        arbitrage_margin = (current_grid_sell_price / current_grid_buy_price - 1) * 100
        reason = f"low-price charge: buy {current_grid_buy_price:.3f}€/kWh vs sell {current_grid_sell_price:.3f}€/kWh ({arbitrage_margin:.1f}% margin), battery at {energy_stored_ratio*100:.1f}%"
        return max_charge, reason
    
    if (current_grid_sell_price > current_grid_buy_price * self.price_ratio_threshold and 
        energy_stored_ratio > 0.35):
      dischargeable = current_energy_stored_kwh * self.discharge_efficiency
      
      if net_grid_demand < -0.5 and dischargeable > 1.0:
        discharge_amount = min(dischargeable, 5.0, abs(net_grid_demand) * 0.8)
        if discharge_amount > 0.5:
          revenue = discharge_amount * current_grid_sell_price
          reason = f"discharge to high sell price {current_grid_sell_price:.3f}€/kWh (vs buy {current_grid_buy_price:.3f}€/kWh), revenue {revenue:.2f}€, battery at {energy_stored_ratio*100:.1f}%"
          return -discharge_amount, reason
      
      elif current_demand_kw > 0 and dischargeable > 0.5:
        demand_coverage = min(current_demand_kw * 0.5, dischargeable, 5.0)
        if demand_coverage > 0.3:
          savings = demand_coverage * (current_grid_buy_price - current_grid_sell_price)
          reason = f"discharge to offset high buy price {current_grid_buy_price:.3f}€/kWh while sell is high {current_grid_sell_price:.3f}€/kWh, savings {savings:.2f}€, battery at {energy_stored_ratio*100:.1f}%"
          return -demand_coverage, reason
    
    if current_demand_kw > current_pv_generation_kw and energy_stored_ratio > 0.25:
      demand_gap = current_demand_kw - current_pv_generation_kw
      dischargeable = current_energy_stored_kwh * self.discharge_efficiency
      
      if dischargeable > demand_gap * 0.3:
        discharge_amount = min(dischargeable, demand_gap * 0.4, 5.0)
        if discharge_amount > 0.3:
          cost_avoided = discharge_amount * current_grid_buy_price
          reason = f"discharge for demand: avoid grid purchase at {current_grid_buy_price:.3f}€/kWh, cost avoided {cost_avoided:.2f}€, battery at {energy_stored_ratio*100:.1f}%"
          return -discharge_amount, reason
    
    if energy_stored_ratio > 0.90:
      excess_storage = current_energy_stored_kwh - (battery_capacity_kwh * 0.80)
      discharge_to_target = min(excess_storage * self.discharge_efficiency, 3.0)
      if discharge_to_target > 0.5:
        reason = f"discharge excess: battery full at {energy_stored_ratio*100:.1f}%, reduce to 80% target"
        return -discharge_to_target, reason
    
    action_kw = 0.0
    reason = f"no action: buy {current_grid_buy_price:.3f}€/kWh, sell {current_grid_sell_price:.3f}€/kWh (ratio {current_grid_sell_price/current_grid_buy_price:.2f}x), battery at {energy_stored_ratio*100:.1f}%, demand {current_demand_kw:.2f}kW, PV {current_pv_generation_kw:.2f}kW"
    
    return action_kw, reason
