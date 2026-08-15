class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    self.max_discharge_rate = 5  # kW
    self.max_charge_rate = 10    # kW

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

    # Calculate battery fill ratio
    battery_fill_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Available capacity for charging
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    # Add opportunistic charging during extreme price lows
    if current_grid_buy_price < current_grid_sell_price * 0.5 and battery_fill_ratio < 0.8:
        charge_power = min(self.max_charge_rate, available_capacity)
        if charge_power > 0:
            return charge_power

    # Conservative charging when buy price is moderately low
    if battery_fill_ratio < 0.9 and current_grid_buy_price < current_grid_sell_price * 0.8:
        charge_power = min(self.max_charge_rate, available_capacity)
        if charge_power > 0:
            return charge_power

    # Strategic discharge when sell price is high relative to buy price
    if current_grid_sell_price > current_grid_buy_price * 1.5 and battery_fill_ratio > 0.3:
        discharge_power = min(self.max_discharge_rate, current_energy_stored_kwh)
        if discharge_power > 0:
            return -discharge_power

    # Default: no action
    action_kw = 0.0

    # Return the calculated action
    return action_kw
