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

    # Calculate battery level ratio and deficit factor
    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    deficit_factor = 1.0 - battery_level_ratio

    # Calculate dynamic price thresholds
    dynamic_buy_threshold = 0.12 * (1.0 - 0.5 * deficit_factor)
    dynamic_sell_threshold = 0.20 * (1.0 + 0.3 * deficit_factor)

    # Layer 1: Demand coverage with PV and stored energy
    remaining_demand = current_demand_kw - current_pv_generation_kw

    if remaining_demand > 0:
      # Need to source additional energy from battery or grid
      if current_energy_stored_kwh > 0 and battery_level_ratio > 0.1:
        # Discharge battery for demand coverage if not too depleted
        discharge_amount = min(remaining_demand, 5.0, current_energy_stored_kwh)
        action_kw = -discharge_amount
        remaining_demand -= discharge_amount

      # If still deficit, buy from grid
      if remaining_demand > 0.01:
        # Buy from grid to cover remaining demand
        pass

    # Layer 2: Opportunistic charging when price is low and battery not full
    elif remaining_demand <= 0 and current_energy_stored_kwh < battery_capacity_kwh * 0.95:
      if current_grid_buy_price < dynamic_buy_threshold:
        # Opportunistic charge: battery is not full and price is favorable
        available_charge_capacity = min(10.0, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = available_charge_capacity

    # Layer 3: Opportunistic discharging when price is high and battery has charge
    if action_kw == 0.0 and current_energy_stored_kwh > battery_capacity_kwh * 0.25:
      if current_grid_sell_price > dynamic_sell_threshold and current_demand_kw < current_pv_generation_kw:
        # Opportunistic discharge: battery has charge and price is favorable
        available_discharge = min(5.0, current_energy_stored_kwh)
        action_kw = -available_discharge

    # Layer 4: Ensure battery stays within safe bounds
    new_energy = current_energy_stored_kwh + action_kw
    if new_energy > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_energy < 0:
      action_kw = -current_energy_stored_kwh

    # Enforce charge/discharge rate limits
    action_kw = max(-5.0, min(10.0, action_kw))

    return action_kw
