class Policy:
  def __init__(self):
    self.max_discharge_rate = 5  # kW
    self.max_charge_rate = 10    # kW
    self.target_fill_low = 0.25  # Discharge target minimum
    self.target_fill_high = 0.75 # Charging target maximum
    self.aggressive_pv_threshold = 0.1  # Charge even at modest margins
    self.price_momentum_buffer = 0.05  # Track price movement

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    battery_fill_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    # Priority 1: Absorb excess PV generation
    if current_pv_generation_kw > current_demand_kw + 1.0:
      excess_pv = current_pv_generation_kw - current_demand_kw
      if battery_fill_ratio < 0.85:
        charge_power = min(self.max_charge_rate, excess_pv * 0.9, available_capacity)
        if charge_power > 1.0:
          return charge_power

    # Priority 2: Charge at favorable prices (more aggressive thresholds)
    price_ratio = current_grid_buy_price / current_grid_sell_price if current_grid_sell_price > 0 else 1.0
    if price_ratio < 0.75 and battery_fill_ratio < self.target_fill_high:
      charge_power = min(self.max_charge_rate, available_capacity * 0.8)
      if charge_power > 0.5:
        return charge_power

    # Priority 3: Demand shifting - discharge during high-demand periods at lower costs
    if current_demand_kw > 2.0 and battery_fill_ratio > self.target_fill_low:
      if current_grid_buy_price > current_grid_sell_price * 0.9:
        discharge_power = min(self.max_discharge_rate, current_energy_stored_kwh * 0.6)
        if discharge_power > 0.5:
          return -discharge_power

    # Priority 4: Opportunistic discharge when spread is favorable
    if current_grid_sell_price > current_grid_buy_price * 1.2 and battery_fill_ratio > self.target_fill_low:
      discharge_power = min(self.max_discharge_rate, current_energy_stored_kwh * 0.5)
      if discharge_power > 0.5:
        return -discharge_power

    # Default: Maintain moderate battery level for flexibility
    if battery_fill_ratio < 0.4:
      charge_power = min(self.max_charge_rate, available_capacity * 0.3)
      if charge_power > 0.5:
        return charge_power
    elif battery_fill_ratio > 0.8:
      discharge_power = min(self.max_discharge_rate, current_energy_stored_kwh * 0.3)
      if discharge_power > 0.5:
        return -discharge_power

    return 0.0
