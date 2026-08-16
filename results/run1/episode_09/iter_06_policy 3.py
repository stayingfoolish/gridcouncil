class Policy:
  def __init__(self):
    self.margin_history = []
    self.price_buy_history = []
    self.price_sell_history = []
    self.soc_history = []
    self.demand_history = []
    self.action_count = 0
    self.last_action = None
    self.consecutive_actions = 0

  def calculate_price_momentum(self, price_history, window=20):
    if len(price_history) < window:
      return 0.0
    recent = price_history[-5:]
    historical = price_history[-window:-5]
    if not historical or not recent:
      return 0.0
    recent_slope = (recent[-1] - recent[0]) / max(0.01, abs(recent[0]))
    historical_slope = (historical[-1] - historical[0]) / max(0.01, abs(historical[0]))
    return (recent_slope - historical_slope) / max(0.01, abs(historical_slope))

  def predict_margin_direction(self, buy_prices, sell_prices):
    if len(buy_prices) < 15:
      return 0.0
    buy_momentum = self.calculate_price_momentum(buy_prices)
    sell_momentum = self.calculate_price_momentum(sell_prices)
    return sell_momentum - buy_momentum

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
    CHARGING_THRESHOLD = 0.3

    self.price_buy_history.append(current_grid_buy_price)
    self.price_sell_history.append(current_grid_sell_price)
    self.soc_history.append(current_energy_stored_kwh / battery_capacity_kwh)
    self.demand_history.append(current_demand_kw)

    for hist in [self.price_buy_history, self.price_sell_history, self.soc_history, self.demand_history]:
      if len(hist) > 96:
        hist.pop(0)

    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    current_margin = current_grid_sell_price - current_grid_buy_price

    if soc_ratio < 0.10:
      return min(max_charge_rate * 0.8, battery_capacity_kwh - current_energy_stored_kwh)

    if soc_ratio > 0.95:
      return -min(max_discharge_rate * 0.7, current_energy_stored_kwh)

    margin_direction = self.predict_margin_direction(self.price_buy_history, self.price_sell_history)

    if len(self.price_buy_history) >= 20:
      recent_prices = self.price_buy_history[-20:]
      avg_price = sum(recent_prices) / len(recent_prices)
      volatility = sum((p - avg_price) ** 2 for p in recent_prices) / len(recent_prices)
      volatility = (volatility ** 0.5) / max(avg_price, 0.01)
    else:
      volatility = 0.0

    min_volatility_threshold = 0.08
    strong_momentum_threshold = 0.15

    if volatility > min_volatility_threshold and margin_direction > strong_momentum_threshold and soc_ratio < 0.75:
      available_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_amount = min(
        max_charge_rate * 0.7,
        available_space,
        current_pv_generation_kw + 3.0
      )
      if charge_amount > CHARGING_THRESHOLD:
        self.action_count += 1
        self.last_action = 'charge'
        return charge_amount

    elif volatility > min_volatility_threshold and margin_direction < -strong_momentum_threshold and soc_ratio > 0.30:
      discharge_amount = min(
        max_discharge_rate * 0.65,
        current_energy_stored_kwh
      )
      if discharge_amount > CHARGING_THRESHOLD:
        self.action_count += 1
        self.last_action = 'discharge'
        return -discharge_amount

    if len(self.price_buy_history) >= 20:
      historical_margins = [s - b for s, b in zip(self.price_sell_history[-20:], self.price_buy_history[-20:])]
      avg_margin = sum(historical_margins) / len(historical_margins)
      margin_std = (sum((m - avg_margin) ** 2 for m in historical_margins) / len(historical_margins)) ** 0.5

      if current_margin > avg_margin + 1.5 * margin_std and soc_ratio > 0.25:
        discharge = min(max_discharge_rate * 0.75, current_energy_stored_kwh)
        if discharge > CHARGING_THRESHOLD:
          return -discharge

      if current_margin < avg_margin - 1.5 * margin_std and soc_ratio < 0.80:
        space = battery_capacity_kwh - current_energy_stored_kwh
        charge = min(max_charge_rate * 0.75, space, current_pv_generation_kw + 4.0)
        if charge > CHARGING_THRESHOLD:
          return charge

    if len(self.demand_history) >= 20:
      avg_demand = sum(self.demand_history[-20:]) / 20
      if current_demand_kw > avg_demand * 1.4 and soc_ratio > 0.30 and current_margin > -0.1:
        discharge = min(max_discharge_rate * 0.5, current_energy_stored_kwh)
        if discharge > CHARGING_THRESHOLD:
          return -discharge

    return 0.0
