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
    charge_rate_kw = battery_capacity_kwh * 0.3
    discharge_rate_kw = battery_capacity_kwh * 0.35

    energy_deficit = current_demand_kw - current_pv_generation_kw

    if current_grid_buy_price > 0:
      price_ratio = current_grid_sell_price / current_grid_buy_price
    else:
      price_ratio = 1.0

    action_kw = 0.0

    if price_ratio > 1.2 and current_energy_stored_kwh > 0:
      action_kw = -min(discharge_rate_kw, current_energy_stored_kwh)

    elif price_ratio <= 1.0 and current_energy_stored_kwh < battery_capacity_kwh:
      action_kw = min(charge_rate_kw, battery_capacity_kwh - current_energy_stored_kwh)

    elif energy_deficit > 0:
      if current_energy_stored_kwh > 0:
        discharge_for_demand = min(energy_deficit, discharge_rate_kw, current_energy_stored_kwh)
        action_kw = -discharge_for_demand

    elif energy_deficit < 0 and current_energy_stored_kwh < battery_capacity_kwh:
      charge_available = min(-energy_deficit, charge_rate_kw, battery_capacity_kwh - current_energy_stored_kwh)
      action_kw = charge_available

    return action_kw
