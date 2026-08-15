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
    discharge_rate_kw = battery_capacity_kwh * 0.2

    space_in_battery = battery_capacity_kwh - current_energy_stored_kwh
    excess_pv = current_pv_generation_kw - current_demand_kw

    if excess_pv > 0:
      if space_in_battery > 0:
        charge_amount = min(charge_rate_kw, excess_pv, space_in_battery)
        return charge_amount
      else:
        return 0.0

    deficit_demand = current_demand_kw - current_pv_generation_kw

    if deficit_demand > 0:
      if current_energy_stored_kwh > 0:
        discharge_amount = min(discharge_rate_kw, deficit_demand, current_energy_stored_kwh)
        return -discharge_amount
      else:
        return 0.0

    price_ratio = current_grid_sell_price / max(current_grid_buy_price, 0.001)

    if price_ratio > 1.2 and current_energy_stored_kwh > 0:
      discharge_amount = min(discharge_rate_kw, current_energy_stored_kwh)
      return -discharge_amount

    if price_ratio < 0.8 and space_in_battery > 0:
      charge_amount = min(charge_rate_kw, space_in_battery)
      return charge_amount

    return 0.0
