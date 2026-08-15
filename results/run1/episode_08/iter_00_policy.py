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

    available_from_pv = current_pv_generation_kw
    net_demand = current_demand_kw - available_from_pv

    price_ratio = current_grid_buy_price / (current_grid_sell_price + 0.001)
    battery_fill_ratio = current_energy_stored_kwh / (battery_capacity_kwh + 0.001)

    if net_demand > 0:
      if current_energy_stored_kwh > 0 and current_grid_buy_price > current_grid_sell_price * 1.5:
        discharge_power = min(max_discharge_rate, current_energy_stored_kwh, net_demand)
        action_kw = -discharge_power
      else:
        action_kw = 0.0
    else:
      surplus_pv = available_from_pv - current_demand_kw

      if battery_fill_ratio < 0.9 and current_grid_buy_price < current_grid_sell_price * 0.8:
        charge_power = min(max_charge_rate, battery_capacity_kwh - current_energy_stored_kwh, surplus_pv * 0.5)
        action_kw = charge_power
      elif battery_fill_ratio < 0.7 and surplus_pv > 1.0:
        charge_power = min(max_charge_rate, battery_capacity_kwh - current_energy_stored_kwh, surplus_pv)
        action_kw = charge_power
      elif battery_fill_ratio > 0.95 and surplus_pv > 0.5:
        action_kw = 0.0
      else:
        action_kw = 0.0

    action_kw = max(-max_discharge_rate, min(max_charge_rate, action_kw))

    return action_kw
