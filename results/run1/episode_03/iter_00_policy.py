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
    # Calculate net demand after PV generation
    net_demand = current_demand_kw - current_pv_generation_kw

    # Maximum charge/discharge rates
    max_charge_rate = 10.0  # kW
    max_discharge_rate = 5.0  # kW

    # Battery state of charge ratio
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Price-based decision logic
    price_ratio = current_grid_buy_price / current_grid_sell_price if current_grid_sell_price > 0 else float('inf')

    action_kw = 0.0

    if net_demand > 0:
      # Household needs energy (demand exceeds PV generation)
      # Decision: use battery if cheap, buy from grid if expensive, or combination

      # Battery is economical to discharge if buying from grid is more expensive
      if current_energy_stored_kwh > 0 and current_grid_buy_price > current_grid_sell_price * 1.1:
        # Grid is expensive, prefer battery discharge
        discharge_power = min(net_demand, max_discharge_rate, current_energy_stored_kwh)
        action_kw = -discharge_power
      elif current_energy_stored_kwh > battery_capacity_kwh * 0.3:
        # Battery has reasonable charge, use it to reduce grid purchases
        discharge_power = min(net_demand * 0.5, max_discharge_rate, current_energy_stored_kwh)
        action_kw = -discharge_power
      else:
        # Battery is low, let grid supply most/all
        action_kw = 0.0

    elif net_demand < 0:
      # Surplus PV generation (PV exceeds demand)
      excess_power = -net_demand

      # Decision: charge battery if cheap, sell to grid if profitable
      if current_energy_stored_kwh < battery_capacity_kwh * 0.9:
        # Battery not full, prioritize charging
        charge_power = min(excess_power, max_charge_rate, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_power
      elif price_ratio > 1.3 and current_energy_stored_kwh < battery_capacity_kwh:
        # Grid buy price is much higher than sell price, still worth charging
        charge_power = min(excess_power * 0.3, max_charge_rate, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_power
      else:
        # Battery is full or not profitable to charge, sell to grid or waste
        action_kw = 0.0

    else:
      # Demand equals PV generation perfectly
      action_kw = 0.0

    # Enforce absolute limits
    action_kw = max(-max_discharge_rate, min(max_charge_rate, action_kw))

    # Final validation: ensure action doesn't violate energy conservation
    if action_kw > 0:
      # Charging: ensure we don't exceed battery capacity
      max_charge = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(action_kw, max_charge)
    elif action_kw < 0:
      # Discharging: ensure we don't exceed stored energy
      max_discharge = current_energy_stored_kwh
      action_kw = max(action_kw, -max_discharge)

    return action_kw
