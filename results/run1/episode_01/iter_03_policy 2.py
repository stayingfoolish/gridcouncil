class Policy:
  def __init__(self):
    """Trend-driven position trading for batteries"""
    self.price_history = []
    self.max_history = 30
    self.action_history = []
    self.position = 0
    self.trend_strength = 0

  def calculate_trend_metrics(self):
    """Calculate momentum and trend confidence"""
    if len(self.price_history) < 5:
      return 0, 0

    recent_prices = self.price_history[-5:]
    momentum = (recent_prices[-1] - recent_prices[0]) / (recent_prices[0] + 0.01)

    price_changes = [recent_prices[i+1] - recent_prices[i] for i in range(len(recent_prices)-1)]
    trend_strength = len([x for x in price_changes if x * momentum > 0]) / len(price_changes)

    return momentum, trend_strength

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Trend-driven position trading strategy"""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    net_power = current_pv_generation_kw - current_demand_kw
    price_spread = current_grid_sell_price - current_grid_buy_price
    momentum, trend_strength = self.calculate_trend_metrics()

    action_kw = 0.0

    if price_spread > 0.05 and current_soc > 0.15:
      discharge_power = min(14.0, current_energy_stored_kwh, battery_capacity_kwh * 0.3)
      action_kw = -discharge_power
      self.position = -1
      return action_kw

    if trend_strength > 0.6:
      if momentum > 0.02:
        if current_soc > 0.20:
          action_kw = -min(12.0, current_energy_stored_kwh * 0.35)
          self.position = -1
          return action_kw

      elif momentum < -0.02:
        if current_soc < 0.80:
          available = battery_capacity_kwh - current_energy_stored_kwh
          action_kw = min(14.0, available * 0.35)
          self.position = 1
          return action_kw

    if current_soc > 0.75 and price_spread > 0.02:
      action_kw = -min(10.0, current_energy_stored_kwh * 0.25)
      return action_kw

    if net_power > 1.0 and current_soc < 0.85:
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(net_power * 0.8, 12.0, available_capacity)
      self.position = 1

    elif net_power < -1.0 and current_soc > 0.15:
      deficit = -net_power
      action_kw = -min(deficit * 0.9, 14.0, current_energy_stored_kwh * 0.4)
      self.position = -1

    elif action_kw == 0 and current_soc < 0.60 and current_grid_buy_price < 0.50:
      available = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(12.0, available * 0.4)
      self.position = 1

    max_charge = battery_capacity_kwh - current_energy_stored_kwh
    max_discharge = current_energy_stored_kwh * 0.4

    if action_kw > 0:
      action_kw = min(action_kw, max_charge)
    else:
      action_kw = max(action_kw, -max_discharge)

    return action_kw
