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
    PRICE_OPPORTUNITY_THRESHOLD = 0.65
    MAX_CHARGE_POWER = 10.0
    MAX_DISCHARGE_POWER = 5.0
    MIN_SOC_FOR_DISCHARGE = 0.35

    generation_demand_diff = current_pv_generation_kw - current_demand_kw
    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    can_charge = max(0, battery_capacity_kwh - current_energy_stored_kwh)
    can_discharge = current_energy_stored_kwh

    action_kw = 0.0

    # PV-driven charging: charge when generation exceeds demand
    if generation_demand_diff > 0:
      excess_generation = generation_demand_diff
      if current_energy_stored_kwh < battery_capacity_kwh * 0.85:
        action_kw = min(excess_generation, MAX_CHARGE_POWER, can_charge)

    # Discharge when demand exceeds generation
    elif generation_demand_diff < 0:
      demand_deficit = -generation_demand_diff

      if current_soc > MIN_SOC_FOR_DISCHARGE:
        if current_grid_sell_price > current_grid_buy_price * 1.4:
          discharge_power = min(demand_deficit, MAX_DISCHARGE_POWER, can_discharge)
          action_kw = -discharge_power
        else:
          discharge_power = min(demand_deficit, MAX_DISCHARGE_POWER, can_discharge)
          action_kw = -discharge_power

    # Price-based opportunistic charging from grid
    if action_kw == 0 and current_grid_buy_price < current_grid_sell_price * PRICE_OPPORTUNITY_THRESHOLD:
      if current_energy_stored_kwh < battery_capacity_kwh * 0.80:
        action_kw = min(MAX_CHARGE_POWER, can_charge)

    return action_kw
