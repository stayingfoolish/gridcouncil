class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    self.price_margin = 0.01

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
    action_kw = 0.0

    pv_after_demand = current_pv_generation_kw - current_demand_kw

    if pv_after_demand > 0:
      if current_grid_buy_price < current_grid_sell_price - self.price_margin:
        available_capacity = battery_capacity_kwh - current_energy_stored_kwh
        if available_capacity > 0:
          action_kw = min(10.0, pv_after_demand)
        else:
          action_kw = 0.0
      else:
        action_kw = 0.0
    elif pv_after_demand < 0:
      demand_deficit = abs(pv_after_demand)
      if current_grid_buy_price > current_grid_sell_price + self.price_margin:
        if current_energy_stored_kwh > 0:
          discharge_amount = min(5.0, current_energy_stored_kwh)
          action_kw = -min(discharge_amount, demand_deficit)
        else:
          action_kw = 0.0
      else:
        if current_energy_stored_kwh > 0:
          discharge_amount = min(5.0, current_energy_stored_kwh)
          action_kw = -min(discharge_amount, demand_deficit)
        else:
          action_kw = 0.0
    else:
      action_kw = 0.0

    return action_kw
