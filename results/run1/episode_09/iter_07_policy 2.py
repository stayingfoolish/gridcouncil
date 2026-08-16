class Policy:
  def __init__(self):
    self.price_buy_history = []
    self.price_sell_history = []
    self.soc_history = []
    self.demand_history = []
    self.regime = 'neutral'

  def predict_next_price(self, price_history, window=10):
    """Linear regression forecast of next price"""
    if len(price_history) < window:
      return price_history[-1] if price_history else 0.0

    recent = price_history[-window:]
    x_vals = list(range(len(recent)))
    x_mean = sum(x_vals) / len(x_vals)
    y_mean = sum(recent) / len(recent)

    numerator = sum((x_vals[i] - x_mean) * (recent[i] - y_mean) for i in range(len(recent)))
    denominator = sum((x_vals[i] - x_mean) ** 2 for i in range(len(recent)))

    if denominator == 0:
      return recent[-1]
    slope = numerator / denominator
    predicted = y_mean + slope * (window - 1)
    return predicted

  def calculate_confidence(self, price_history, window=12):
    """Volatility-based confidence in prediction"""
    if len(price_history) < window:
      return 0.5
    recent = price_history[-window:]
    mean = sum(recent) / len(recent)
    variance = sum((p - mean) ** 2 for p in recent) / len(recent)
    volatility = (variance ** 0.5) / max(mean, 0.01)
    return 1.0 / (1.0 + volatility * 3.0)

  def identify_regime(self):
    """Market regime classification"""
    if len(self.price_buy_history) < 8:
      return 'neutral'
    recent_buy = self.price_buy_history[-6:]
    buy_slope = recent_buy[-1] - recent_buy[0]
    if buy_slope > 0.4:
      return 'rising'
    elif buy_slope < -0.4:
      return 'falling'
    return 'neutral'

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    MAX_CHARGE = 10.0
    MAX_DISCHARGE = 8.0
    MIN_ACTION = 0.3

    self.price_buy_history.append(current_grid_buy_price)
    self.price_sell_history.append(current_grid_sell_price)
    self.soc_history.append(current_energy_stored_kwh / battery_capacity_kwh)
    self.demand_history.append(current_demand_kw)

    for hist in [self.price_buy_history, self.price_sell_history, self.soc_history, self.demand_history]:
      if len(hist) > 96:
        hist.pop(0)

    soc = current_energy_stored_kwh / battery_capacity_kwh

    if soc < 0.06:
      return min(MAX_CHARGE * 0.95, battery_capacity_kwh - current_energy_stored_kwh)
    if soc > 0.97:
      return -min(MAX_DISCHARGE * 0.85, current_energy_stored_kwh)

    if len(self.price_buy_history) < 8:
      return 0.0

    next_buy = self.predict_next_price(self.price_buy_history, window=10)
    next_sell = self.predict_next_price(self.price_sell_history, window=10)
    confidence = self.calculate_confidence(self.price_buy_history, window=12)

    current_margin = current_grid_sell_price - current_grid_buy_price
    predicted_margin = next_sell - next_buy
    margin_delta = predicted_margin - current_margin

    self.regime = self.identify_regime()

    if abs(margin_delta) > 0.15 and confidence > 0.55:
      if margin_delta > 0.15 and soc < 0.78:
        space = battery_capacity_kwh - current_energy_stored_kwh
        sizing_factor = 0.65 + confidence * 0.30
        charge = min(MAX_CHARGE * sizing_factor, space, current_pv_generation_kw + 6.0)
        if charge > MIN_ACTION:
          return charge

      elif margin_delta < -0.15 and soc > 0.22:
        sizing_factor = 0.60 + confidence * 0.35
        discharge = min(MAX_DISCHARGE * sizing_factor, current_energy_stored_kwh)
        if discharge > MIN_ACTION:
          return -discharge

    if confidence > 0.60:
      if self.regime == 'falling' and soc > 0.18:
        discharge = min(MAX_DISCHARGE * 0.72, current_energy_stored_kwh)
        if discharge > MIN_ACTION:
          return -discharge
      elif self.regime == 'rising' and soc < 0.82:
        space = battery_capacity_kwh - current_energy_stored_kwh
        charge = min(MAX_CHARGE * 0.68, space)
        if charge > MIN_ACTION:
          return charge

    if len(self.demand_history) >= 15:
      baseline_demand = sum(self.demand_history[-15:]) / 15
      if current_demand_kw > baseline_demand * 1.25 and soc > 0.20:
        if current_margin > -0.08:
          discharge = min(MAX_DISCHARGE * 0.55, current_energy_stored_kwh)
          if discharge > MIN_ACTION:
            return -discharge

    return 0.0
