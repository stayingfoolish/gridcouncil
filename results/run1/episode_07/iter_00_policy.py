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

    # Calculate net power after PV generation
    net_power_after_pv = current_demand_kw - current_pv_generation_kw

    # Define price thresholds for decision making
    price_margin = 0.02

    # Battery state indicators
    battery_capacity_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    action_kw = 0.0

    # Decision logic: minimize cost by buying when cheap, selling when expensive
    # If buying price is lower than selling price + margin, consider charging
    # If selling price is higher than buying price + margin, consider discharging

    if current_grid_buy_price < current_grid_sell_price - price_margin:
      # Grid price is favorable for buying - should charge battery
      if battery_capacity_ratio < 0.95:
        # Battery has room to charge
        # Charge at maximum rate (10 kW) if price is good
        action_kw = 10.0
    elif current_grid_buy_price > current_grid_sell_price + price_margin:
      # Grid price is high - should discharge battery if available
      if current_energy_stored_kwh > 0.1:
        # Have stored energy, discharge at maximum rate (5 kW)
        action_kw = -5.0
    else:
      # Prices are similar, make tactical decision based on demand coverage
      if net_power_after_pv > 0:
        # Net demand exists after PV
        if battery_capacity_ratio > 0.5 and current_grid_buy_price > current_grid_sell_price:
          # Prefer battery discharge over buying if battery is reasonably full and price is high
          discharge_power = min(5.0, current_energy_stored_kwh)
          action_kw = -discharge_power if discharge_power > 0.1 else 0.0
        elif battery_capacity_ratio < 0.8 and current_grid_buy_price < current_grid_sell_price * 1.1:
          # Charge battery when price is reasonable and battery not too full
          action_kw = 10.0
      else:
        # Excess PV generation available
        if battery_capacity_ratio < 0.90:
          # Can store excess PV energy
          action_kw = min(10.0, battery_capacity_kwh - current_energy_stored_kwh)

    return action_kw
