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
    # Calculate battery state as ratio of current to maximum capacity
    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Calculate net energy balance: positive means excess PV, negative means deficit
    net_balance = current_pv_generation_kw - current_demand_kw

    action_kw = 0.0

    # Case 1: Excess PV generation available
    if net_balance > 0:
      # Charge the battery if there is room
      if battery_level_ratio < 0.95:
        max_charge_possible = min(net_balance, 10, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = max_charge_possible

    # Case 2: Deficit in PV generation
    elif net_balance < 0:
      # Prefer discharging battery over grid purchase if battery has sufficient charge
      deficit = abs(net_balance)
      if battery_level_ratio > 0.2:
        max_discharge_possible = min(deficit, 5, current_energy_stored_kwh)
        action_kw = -max_discharge_possible

    # Case 3: Balanced supply and demand
    else:
      # Use price arbitrage: charge when buying is cheap relative to selling
      if current_grid_buy_price < current_grid_sell_price and battery_level_ratio < 0.8:
        charge_amount = min(5, 10, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_amount
      # Discharge when selling price is favorable relative to buying
      elif current_grid_sell_price > current_grid_buy_price * 1.5 and battery_level_ratio > 0.6:
        discharge_amount = min(5, current_energy_stored_kwh)
        action_kw = -discharge_amount

    # Enforce operational constraints
    action_kw = max(-5, min(10, action_kw))

    return action_kw
