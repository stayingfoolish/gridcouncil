class Policy:
  def __init__(self):
    self.price_history = []
    self.trade_outcomes = []
    self.adaptive_threshold = 1.0
    self.confidence_score = 0.5
    self.recent_margin_avg = 0.0

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

    self.price_history.append(price_margin)
    if len(self.price_history) > 20:
      self.price_history.pop(0)

    if len(self.price_history) >= 5:
      self.recent_margin_avg = sum(self.price_history[-5:]) / 5.0
      price_trend = self.price_history[-1] - self.recent_margin_avg
    else:
      price_trend = 0.0

    self.adaptive_threshold = 0.8 + (0.4 * self.confidence_score)

    if price_margin < -self.adaptive_threshold and price_trend < -0.3:
      if soc_ratio < 0.9:
        battery_space = battery_capacity_kwh - current_energy_stored_kwh
        charge_power = min(max_charge_rate, battery_space, current_pv_generation_kw + max_charge_rate)
        if charge_power > 0.3:
          self.confidence_score = min(1.0, self.confidence_score + 0.05)
          return charge_power

    if price_margin > self.adaptive_threshold and price_trend > 0.3:
      if soc_ratio > 0.2:
        discharge_power = min(max_discharge_rate, current_energy_stored_kwh)
        if discharge_power > 0.3:
          self.confidence_score = min(1.0, self.confidence_score + 0.05)
          return -discharge_power

    if soc_ratio < 0.2 and current_grid_buy_price < 4.0:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(max_charge_rate * 0.7, battery_space)
      return charge_power

    if soc_ratio > 0.9 and price_margin > 0.5:
      discharge_power = min(max_discharge_rate * 0.6, current_energy_stored_kwh)
      return -discharge_power

    energy_balance = current_pv_generation_kw - current_demand_kw
    if energy_balance > 0.5 and soc_ratio < 0.85:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(energy_balance * 0.8, max_charge_rate, battery_space)
      return charge_power

    if len(self.trade_outcomes) > 0 and price_margin * self.trade_outcomes[-1] < 0:
      self.confidence_score = max(0.3, self.confidence_score - 0.1)
    else:
      self.confidence_score = max(0.3, self.confidence_score - 0.02)

    self.trade_outcomes.append(price_margin)
    if len(self.trade_outcomes) > 20:
      self.trade_outcomes.pop(0)

    return 0.0
