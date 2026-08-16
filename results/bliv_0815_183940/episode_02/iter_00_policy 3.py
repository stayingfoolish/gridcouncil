class Policy:
  def __init__(self):
    """Initializes the policy with parameters for battery management strategy."""
    self.charge_threshold = 0.3
    self.discharge_threshold = 0.7
    self.price_threshold_ratio = 1.2

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state.

    Returns:
      float: The target power for the battery [kW]
        positive: charging; negative: discharging;
        zero: no action
    """
    
    max_charge_power = 10.0
    max_discharge_power = 5.0
    
    net_generation = current_pv_generation_kw - current_demand_kw
    battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    
    if net_generation > 0:
      available_to_store = net_generation
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      
      if battery_space > 0 and available_to_store > 0:
        can_charge = min(available_to_store, max_charge_power, battery_space)
        if battery_soc < self.charge_threshold or current_grid_buy_price > current_grid_sell_price * 0.5:
          return can_charge
        elif current_grid_sell_price > current_grid_buy_price * 0.1:
          return 0
        else:
          return min(can_charge * 0.5, battery_space / 2)
      return 0
    
    else:
      deficit = -net_generation
      
      if battery_soc > self.discharge_threshold and deficit > 0:
        can_discharge = min(deficit, max_discharge_power, current_energy_stored_kwh)
        if current_grid_buy_price > current_grid_sell_price * self.price_threshold_ratio:
          return -can_discharge
      
      if battery_soc > 0.8 and current_grid_sell_price > current_grid_buy_price * 1.5:
        can_discharge = min(max_discharge_power, current_energy_stored_kwh)
        return -can_discharge * 0.3
      
      return 0
