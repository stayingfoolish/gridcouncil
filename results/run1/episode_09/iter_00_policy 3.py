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

    energy_balance = current_pv_generation_kw - current_demand_kw

    if energy_balance < 0:
      power_deficit = -energy_balance
      discharge_power = min(power_deficit, max_discharge_rate, current_energy_stored_kwh)
      return -discharge_power
    else:
      power_surplus = energy_balance
      battery_space = battery_capacity_kwh - current_energy_stored_kwh

      if battery_space > 0 and power_surplus > 0:
        charge_power = min(power_surplus, max_charge_rate, battery_space)
        return charge_power
      else:
        return 0.0
