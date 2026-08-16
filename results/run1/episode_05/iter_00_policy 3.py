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
    # --- Implement your logic here ---

    # Calculate energy surplus or deficit
    pv_surplus = current_pv_generation_kw - current_demand_kw

    # Battery state thresholds
    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Price comparison threshold
    price_diff = current_grid_sell_price - current_grid_buy_price

    action_kw = 0.0

    # Strategy 1: Charge when prices are low and PV is available
    if pv_surplus > 0 and current_grid_buy_price < current_grid_sell_price:
        # PV surplus available and buying is cheaper than selling
        # Charge to store excess PV energy for later use
        charge_capacity = min(pv_surplus, 10.0)  # Respect power_charge <= 10
        charge_space = battery_capacity_kwh - current_energy_stored_kwh
        if charge_space > 0:
            action_kw = min(charge_capacity, charge_space / 1.0)  # Assume 1-hour discretization

    # Strategy 2: Discharge when demand exceeds PV and discharge is economical
    elif pv_surplus < 0 and battery_level_ratio > 0.2:
        # Demand exceeds PV, consider discharging
        deficit = abs(pv_surplus)

        # Discharge if: battery has energy AND (selling price is high OR buying is expensive)
        if current_grid_sell_price > current_grid_buy_price * 1.1 or current_grid_buy_price > 0.15:
            discharge_power = min(deficit, 5.0)  # Respect power_discharge >= -5
            discharge_available = current_energy_stored_kwh / 1.0  # Assume 1-hour discretization
            action_kw = -min(discharge_power, discharge_available)

    # Strategy 3: Charge when prices are very low (off-peak)
    if action_kw == 0 and current_grid_buy_price < 0.08 and battery_level_ratio < 0.9:
        # Off-peak period, charge battery for future use
        charge_space = battery_capacity_kwh - current_energy_stored_kwh
        if charge_space > 0:
            action_kw = min(8.0, charge_space / 1.0)

    # Strategy 4: Discharge when prices are very high (peak)
    if action_kw == 0 and current_grid_sell_price > 0.25 and battery_level_ratio > 0.3:
        # Peak pricing, discharge if possible
        discharge_available = current_energy_stored_kwh / 1.0
        if discharge_available > 0:
            action_kw = -min(4.0, discharge_available)

    # Ensure constraints are respected
    action_kw = max(-5.0, min(10.0, action_kw))

    return action_kw
