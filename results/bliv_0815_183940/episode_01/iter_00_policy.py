class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    self.max_charge_power = 10  # kW
    self.max_discharge_power = 5  # kW
    self.price_threshold_charge = 0.15  # euro/kWh - charge if price below this
    self.price_threshold_discharge = 0.25  # euro/kWh - discharge if price above this
    self.battery_min_threshold = 0.2  # Keep at least 20% for emergencies
    self.battery_max_threshold = 0.9  # Don't charge beyond 90%

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
    
    # Calculate available energy from PV
    excess_pv = max(0, current_pv_generation_kw - current_demand_kw)
    deficit = max(0, current_demand_kw - current_pv_generation_kw)
    
    # Calculate battery state percentages
    battery_level = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    
    # Calculate available charging/discharging capacity
    energy_to_full = battery_capacity_kwh - current_energy_stored_kwh
    max_charge_available = min(self.max_charge_power, energy_to_full)
    max_discharge_available = min(self.max_discharge_power, current_energy_stored_kwh)
    
    # Initialize action as no action
    action_kw = 0.0
    
    # Strategy: Price-based decision making with battery management
    
    # Case 1: High PV generation (excess energy available)
    if excess_pv > 0:
        # Check if we should charge the battery
        if battery_level < self.battery_max_threshold:
            # Charge with excess PV if battery not full
            charge_amount = min(excess_pv, max_charge_available)
            action_kw = charge_amount
            return action_kw
        elif current_grid_sell_price > current_grid_buy_price * 0.9:
            # If battery is full and sell price is reasonable, don't charge
            action_kw = 0.0
            return action_kw
    
    # Case 2: Energy deficit (need to cover demand)
    if deficit > 0:
        # Decision: Use battery or buy from grid?
        if current_grid_buy_price > self.price_threshold_discharge and battery_level > self.battery_min_threshold:
            # Grid price is high and battery has energy - discharge
            discharge_amount = min(deficit, max_discharge_available)
            action_kw = -discharge_amount
            return action_kw
        else:
            # Grid price is low or battery is low - buy from grid
            action_kw = 0.0
            return action_kw
    
    # Case 3: No excess PV, no deficit (balanced or slight variations)
    if excess_pv == 0 and deficit == 0:
        # Check battery level and prices for optimization
        if current_grid_buy_price < self.price_threshold_charge and battery_level < self.battery_max_threshold:
            # Low grid price and battery not full - charge
            charge_amount = min(self.max_charge_power, max_charge_available)
            action_kw = charge_amount
            return action_kw
        elif current_grid_sell_price > self.price_threshold_discharge and battery_level > self.battery_min_threshold * 1.5:
            # High sell price and battery has good level - discharge
            discharge_amount = min(self.max_discharge_power * 0.5, max_discharge_available)
            action_kw = -discharge_amount
            return action_kw
    
    # Case 4: Small excess PV but battery already full
    if excess_pv > 0 and battery_level >= self.battery_max_threshold:
        # Don't charge, let excess go to grid or be wasted
        action_kw = 0.0
        return action_kw
    
    # Default: maintain current state
    return action_kw
