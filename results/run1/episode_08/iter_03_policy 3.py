class Policy:
  def __init__(self):
    self.max_discharge_rate = 10
    self.max_charge_rate = 12
    self.charge_threshold = 0.20
    self.discharge_threshold = 0.80
    self.margin_factor = 1.15
    self.pv_boost_threshold = 2.0
    self.price_momentum_window = 3
    self.cycle_aggressiveness = 0.85

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
    price_spread = (current_grid_sell_price - current_grid_buy_price) / current_grid_buy_price if current_grid_buy_price > 0 else 0

    if current_pv_generation_kw > current_demand_kw + self.pv_boost_threshold:
      excess_pv = current_pv_generation_kw - current_demand_kw
      if battery_fill_ratio < 0.95:
        charge_power = min(self.max_charge_rate, excess_pv * 0.95, available_capacity)
        if charge_power > 0.1:
          return charge_power

    if current_grid_sell_price / current_grid_buy_price > self.margin_factor and battery_fill_ratio < 0.80:
      charge_power = min(self.max_charge_rate * 1.1, available_capacity * 0.9)
      if charge_power > 0.2:
        return charge_power

    if current_grid_sell_price / current_grid_buy_price > self.margin_factor and battery_fill_ratio > 0.50:
      discharge_power = min(self.max_discharge_rate, current_energy_stored_kwh * self.cycle_aggressiveness)
      if discharge_power > 0.3:
        return -discharge_power

    if battery_fill_ratio < self.charge_threshold and available_capacity > 2.0:
      charge_power = min(self.max_charge_rate * 0.7, available_capacity * 0.6)
      if charge_power > 0.3:
        return charge_power

    if battery_fill_ratio > self.discharge_threshold and current_energy_stored_kwh > 5.0:
      discharge_power = min(self.max_discharge_rate * 0.8, current_energy_stored_kwh * 0.5)
      if discharge_power > 0.3:
        return -discharge_power

    if current_grid_buy_price < 0.12 and battery_fill_ratio < 0.75:
      charge_power = min(self.max_charge_rate, available_capacity * 0.7)
      if charge_power > 0.2:
        return charge_power

    return 0.0
