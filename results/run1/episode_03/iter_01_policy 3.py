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
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    net_demand = current_demand_kw - current_pv_generation_kw
    action_kw = 0.0

    if net_demand > 0:
      if current_energy_stored_kwh > battery_capacity_kwh * 0.15:
        price_spread = current_grid_buy_price - current_grid_sell_price
        normalized_spread = price_spread / current_grid_sell_price if current_grid_sell_price > 0 else 0

        discharge_proportion = min(1.0, 0.3 + 0.7 * (normalized_spread / 0.4))

        discharge_power = min(net_demand * discharge_proportion, max_discharge_rate, current_energy_stored_kwh)
        action_kw = -discharge_power
      else:
        action_kw = 0.0

    elif net_demand < 0:
      excess_energy = -net_demand

      if current_grid_sell_price > current_grid_buy_price * 0.95:
        charge_from_pv = min(excess_energy, max_charge_rate, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_from_pv
      else:
        action_kw = 0.0

    else:
      action_kw = 0.0

    return action_kw
