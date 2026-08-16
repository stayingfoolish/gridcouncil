class Policy:
  def __init__(self):
    pass

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    max_charge_rate = 10.0
    max_discharge_rate = 5.0
    price_margin_threshold = 2.0

    energy_balance = current_pv_generation_kw - current_demand_kw
    price_margin = current_grid_sell_price - current_grid_buy_price

    if price_margin > price_margin_threshold and current_energy_stored_kwh > 0:
      discharge_power = min(max_discharge_rate, current_energy_stored_kwh)
      return -discharge_power

    if energy_balance > 0 and current_grid_buy_price < current_grid_sell_price:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      if battery_space > 0:
        charge_power = min(energy_balance, max_charge_rate, battery_space)
        return charge_power

    if energy_balance < 0:
      power_deficit = -energy_balance
      discharge_power = min(power_deficit, max_discharge_rate, current_energy_stored_kwh)
      return -discharge_power

    return 0.0
