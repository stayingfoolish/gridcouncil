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

    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    if current_pv_generation_kw >= current_demand_kw:
        excess_pv = current_pv_generation_kw - current_demand_kw
        charge_space = battery_capacity_kwh - current_energy_stored_kwh
        if charge_space > 0 and excess_pv > 0:
            action_kw = min(10.0, excess_pv, charge_space / 1.0)

    if action_kw == 0 and current_pv_generation_kw < current_demand_kw:
        demand_deficit = current_demand_kw - current_pv_generation_kw
        discharge_available = current_energy_stored_kwh / 1.0
        if discharge_available > 0 and battery_level_ratio > 0.1:
            action_kw = -min(5.0, discharge_available, demand_deficit)

    if action_kw == 0 and current_grid_buy_price < 0.12 and battery_level_ratio < 0.95:
        charge_space = battery_capacity_kwh - current_energy_stored_kwh
        if charge_space > 0:
            action_kw = min(10.0, charge_space / 1.0)

    if action_kw == 0 and current_grid_sell_price > 0.20 and battery_level_ratio > 0.25:
        discharge_available = current_energy_stored_kwh / 1.0
        if discharge_available > 0:
            action_kw = -min(5.0, discharge_available)

    action_kw = max(-5.0, min(10.0, action_kw))

    if action_kw < 0:
        max_discharge = current_energy_stored_kwh / 1.0
        action_kw = max(-max_discharge, action_kw)

    if action_kw > 0:
        max_charge = battery_capacity_kwh - current_energy_stored_kwh
        action_kw = min(max_charge / 1.0, action_kw)

    return action_kw
