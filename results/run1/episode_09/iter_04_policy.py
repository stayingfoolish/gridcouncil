class Policy:
  def __init__(self):
    self.margin_history = []
    self.trade_history = []

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    max_charge_rate = 10.0
    max_discharge_rate = 8.0

    margin = current_grid_sell_price - current_grid_buy_price
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh

    self.margin_history.append(margin)
    if len(self.margin_history) > 100:
      self.margin_history.pop(0)

    if len(self.margin_history) >= 10:
      sorted_margins = sorted(self.margin_history)
      buy_threshold = sorted_margins[max(0, int(len(sorted_margins) * 0.25))]
      sell_threshold = sorted_margins[min(len(sorted_margins)-1, int(len(sorted_margins) * 0.75))]
    else:
      buy_threshold = -0.5
      sell_threshold = 0.5

    if margin < buy_threshold and soc_ratio < 0.95:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      buy_strength = (buy_threshold - margin) / max(abs(buy_threshold), 0.1)
      charge_power = min(
        max_charge_rate * (0.7 + 0.3 * min(1.0, buy_strength)),
        battery_space,
        current_pv_generation_kw + max_charge_rate
      )
      if charge_power > 0.2:
        self.trade_history.append(('charge', margin))
        return charge_power

    if margin > sell_threshold and soc_ratio > 0.15:
      sell_strength = (margin - sell_threshold) / max(abs(sell_threshold), 0.1)
      discharge_power = min(
        max_discharge_rate * (0.6 + 0.4 * min(1.0, sell_strength)),
        current_energy_stored_kwh
      )
      if discharge_power > 0.2:
        self.trade_history.append(('discharge', margin))
        return -discharge_power

    if soc_ratio < 0.1:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(max_charge_rate * 0.8, battery_space)
      return charge_power

    if soc_ratio > 0.95:
      discharge_power = min(max_discharge_rate * 0.5, current_energy_stored_kwh)
      return -discharge_power

    return 0.0
