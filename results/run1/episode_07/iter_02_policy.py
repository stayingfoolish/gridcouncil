class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    pass

  def take_action(self,
    # energy stored in the battery [kWh]
    current_energy_stored_kwh: float,
    # PV power generation [kW]
    current_pv_generation_kw: float,
    # household power demand [kW]
    current_demand_kw: float,
    # grid purchase price [euro/kWh]
    current_grid_buy_price: float,
    # grid feed-in tariff (sell price) [euro/kWh]
    current_grid_sell_price: float,
    # Maximum battery capacity [kWh]
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state.

    Returns:
      float: The target power for the battery [kW]
        positive: charging; negative: discharging;
        zero: no action
    """

    # Define thresholds for price arbitrage
    charge_threshold = 0.85 * current_grid_sell_price
    discharge_threshold = 1.15 * current_grid_buy_price

    # Maximum charge rate (increased from 10kW to 12kW)
    max_charge_rate = 12.0
    # Maximum discharge rate
    max_discharge_rate = 5.0

    action_kw = 0.0

    # Strategy 1: Price arbitrage - charge when buy price is cheap
    if current_grid_buy_price < charge_threshold:
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      if available_capacity > 0:
        action_kw = min(max_charge_rate, available_capacity)
        return action_kw

    # Strategy 2: Price arbitrage - discharge when sell price is high
    if current_grid_sell_price > discharge_threshold:
      if current_energy_stored_kwh > 0:
        action_kw = -min(max_discharge_rate, current_energy_stored_kwh)
        return action_kw

    # Strategy 3: Use PV surplus for charging at reduced rate
    pv_surplus = current_pv_generation_kw - current_demand_kw
    if pv_surplus > 0:
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      if available_capacity > 0:
        action_kw = min(5.0, pv_surplus, available_capacity)
        return action_kw

    # Strategy 4: Discharge to meet demand if available
    if current_demand_kw > current_pv_generation_kw:
      demand_deficit = current_demand_kw - current_pv_generation_kw
      if current_energy_stored_kwh > 0:
        action_kw = -min(max_discharge_rate, demand_deficit, current_energy_stored_kwh)
        return action_kw

    # Strategy 5: No action
    action_kw = 0.0

    return action_kw
