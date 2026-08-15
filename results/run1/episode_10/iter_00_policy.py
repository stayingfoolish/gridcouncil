class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    pass

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
    battery_space = battery_capacity_kwh - current_energy_stored_kwh
    min_reserve = battery_capacity_kwh * 0.1

    if battery_space > 0:
        if current_grid_buy_price < 0.1:
            action_kw = min(10.0, battery_space)
        elif current_grid_buy_price < 0.2 and battery_space > 1.0:
            action_kw = min(8.0, battery_space * 0.5)
        elif current_pv_generation_kw > current_demand_kw and battery_space > 1.0:
            excess = min(current_pv_generation_kw - current_demand_kw, 10.0)
            action_kw = min(excess, battery_space)

    if action_kw == 0.0 and current_energy_stored_kwh > min_reserve:
        if current_grid_sell_price > 0.4:
            action_kw = -min(5.0, current_energy_stored_kwh - min_reserve)
        elif current_grid_sell_price > 0.3 and current_energy_stored_kwh > battery_capacity_kwh * 0.5:
            action_kw = -min(3.0, (current_energy_stored_kwh - min_reserve) * 0.3)

    return max(-5.0, min(10.0, action_kw))
