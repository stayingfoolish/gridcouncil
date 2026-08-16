class Policy:
  def __init__(self):
    self.price_history = []
    self.max_history_size = 24

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_size:
      self.price_history.pop(0)

    avg_buy_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_grid_buy_price

    demand_deficit = current_demand_kw - current_pv_generation_kw
    excess_pv = max(0, current_pv_generation_kw - current_demand_kw)
    battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    if current_grid_buy_price < avg_buy_price * 0.85:
      if battery_soc < 0.95:
        available_charge = min(10.0, battery_capacity_kwh - current_energy_stored_kwh)
        return available_charge

    if current_grid_buy_price > avg_buy_price * 1.15:
      if demand_deficit > 0 and battery_soc > 0.1:
        discharge_power = min(5.0, current_energy_stored_kwh, demand_deficit)
        return -discharge_power

    if demand_deficit > 0:
      if battery_soc > 0.25:
        discharge_power = min(5.0, current_energy_stored_kwh, demand_deficit)
        return -discharge_power

    if excess_pv > 0 and battery_soc < 0.9:
      max_charge = min(10.0, excess_pv, battery_capacity_kwh - current_energy_stored_kwh)
      return max_charge

    if current_grid_sell_price > avg_buy_price * 1.3:
      if battery_soc > 0.3:
        discharge_power = min(5.0, current_energy_stored_kwh - battery_capacity_kwh * 0.15)
        return -max(0, discharge_power)

    return 0.0
