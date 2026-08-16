class Policy:
  def __init__(self):
    """Initializes the policy with configuration parameters."""
    self.max_charge_rate = 12.0  # kW
    self.max_discharge_rate = 10.0  # Increased from 5.0 to capture peak sell opportunities
    self.min_battery_soc = 0.2  # 20% minimum reserve margin
    self.target_soc = 0.5  # Target 50% state of charge
    self.high_price_threshold = 0.15  # euros/kWh threshold for selling
    self.low_price_threshold = 0.10  # euros/kWh threshold for buying

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state."""

    # Calculate current state of charge
    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Calculate available energy to discharge (respecting minimum reserve)
    min_energy = battery_capacity_kwh * self.min_battery_soc
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)

    # Calculate available capacity to charge
    available_to_charge = max(0, battery_capacity_kwh - current_energy_stored_kwh)

    action_kw = 0.0

    # Strategy 1: Discharge at high sell price (PRIORITIZED FIRST - moved before PV charging)
    if current_grid_sell_price > self.high_price_threshold and available_to_discharge > 0:
      max_discharge = min(self.max_discharge_rate, available_to_discharge)
      action_kw = -max_discharge
      return action_kw

    # Strategy 2: Use surplus PV to charge (MOVED AFTER high-price discharge)
    surplus_pv = max(0, current_pv_generation_kw - current_demand_kw)
    if surplus_pv > 0.1 and available_to_charge > 0:
      charge_amount = min(self.max_charge_rate, surplus_pv, available_to_charge)
      action_kw = charge_amount
      return action_kw

    # Strategy 3: Buy at low price to charge and build reserves
    if current_grid_buy_price < self.low_price_threshold and available_to_charge > 0 and current_soc < self.target_soc:
      action_kw = min(self.max_charge_rate, available_to_charge)
      return action_kw

    # Strategy 4: Discharge to meet demand deficit when battery reserves allow
    deficit = max(0, current_demand_kw - current_pv_generation_kw)
    if deficit > 0.1 and available_to_discharge > 0 and current_soc > self.min_battery_soc * 1.5:
      max_discharge = min(self.max_discharge_rate, available_to_discharge, deficit)
      action_kw = -max_discharge
      return action_kw

    # Default: maintain current state
    return action_kw
