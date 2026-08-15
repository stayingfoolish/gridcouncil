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

    price_margin = current_grid_sell_price - current_grid_buy_price
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh

    charge_price_threshold = 3.5 if price_margin < -1.0 else 5.0

    if price_margin < -2.0:
      target_soc_min = 0.7
      target_soc_max = 1.0
    elif price_margin < -0.5:
      target_soc_min = 0.5
      target_soc_max = 0.85
    elif price_margin > 2.0:
      target_soc_min = 0.0
      target_soc_max = 0.3
    else:
      target_soc_min = 0.35
      target_soc_max = 0.65

    if current_grid_buy_price < charge_price_threshold and soc_ratio < target_soc_max:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      available_power = current_pv_generation_kw + max_charge_rate
      charge_power = min(available_power, max_charge_rate, battery_space)
      if charge_power > 0.5:
        return charge_power

    if price_margin > 1.5 and soc_ratio > target_soc_min:
      discharge_power = min(max_discharge_rate, current_energy_stored_kwh)
      return -discharge_power

    energy_balance = current_pv_generation_kw - current_demand_kw
    if energy_balance < 0:
      power_deficit = -energy_balance
      discharge_power = min(power_deficit, max_discharge_rate, current_energy_stored_kwh)
      if discharge_power > 0.5:
        return -discharge_power

    if energy_balance > 0 and soc_ratio < target_soc_max:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(energy_balance, max_charge_rate, battery_space)
      if charge_power > 0.5:
        return charge_power

    return 0.0
