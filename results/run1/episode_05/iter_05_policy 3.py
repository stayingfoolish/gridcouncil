class Policy:
  def __init__(self):
    self.price_history = []
    self.action_history = []
    self.regret_tracking = {"missed_buy": 0, "missed_sell": 0}
    self.max_history_len = 30
    self.entropy_threshold = 0.15
    self.adaptive_weights = {"arbitrage": 0.5, "soc": 0.3, "pv": 0.2}

  def calculate_price_entropy(self, prices):
    if len(prices) < 5:
      return 0.0

    recent_trend = (prices[-1] - prices[-5]) / (sum(prices[-5:]) / 5 + 0.001)

    price_changes = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    change_avg = sum(price_changes) / len(price_changes) if price_changes else 0.001
    change_variance = sum((c - change_avg)**2 for c in price_changes) / len(price_changes) if price_changes else 0.001
    clustering_factor = (change_variance ** 0.5) / (change_avg + 0.001)

    entropy = abs(recent_trend) * (1.0 + clustering_factor)
    return min(entropy, 1.0)

  def calculate_regret_urgency(self, buy_price, sell_price, current_soc_ratio):
    spread = sell_price - buy_price

    if spread > 0 and current_soc_ratio < 0.8:
      urgency = min((spread / (buy_price + 0.001)) * 2.0, 1.0)
    elif spread < 0 and current_soc_ratio > 0.2:
      urgency = min((abs(spread) / (sell_price + 0.001)) * 2.0, 1.0)
    else:
      urgency = 0.0

    return urgency

  def calculate_adaptive_soc_target(self, entropy, volatility):
    base_low, base_high = 0.30, 0.70

    adjustment = entropy * 0.20

    target_low = max(base_low - adjustment, 0.15)
    target_high = min(base_high + adjustment, 0.85)

    return target_low, target_high

  def update_adaptive_weights(self, entropy, regret_ratio):
    if entropy > self.entropy_threshold:
      self.adaptive_weights["arbitrage"] = 0.60
      self.adaptive_weights["soc"] = 0.20
      self.adaptive_weights["pv"] = 0.20
    else:
      self.adaptive_weights["arbitrage"] = 0.45
      self.adaptive_weights["soc"] = 0.35
      self.adaptive_weights["pv"] = 0.20

    if regret_ratio > 0.05:
      self.adaptive_weights["arbitrage"] = min(self.adaptive_weights["arbitrage"] + 0.10, 0.70)
      self.adaptive_weights["soc"] -= 0.05

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_len:
      self.price_history.pop(0)

    entropy = self.calculate_price_entropy(self.price_history)
    current_soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    regret_ratio = (self.regret_tracking["missed_buy"] + self.regret_tracking["missed_sell"]) / (len(self.price_history) + 1)

    self.update_adaptive_weights(entropy, regret_ratio)
    target_low, target_high = self.calculate_adaptive_soc_target(entropy, entropy)

    price_avg = sum(self.price_history) / len(self.price_history) if self.price_history else current_grid_buy_price
    price_std = (sum((p - price_avg)**2 for p in self.price_history) / len(self.price_history) if self.price_history else 0.01) ** 0.5 + 0.001

    spread = current_grid_sell_price - current_grid_buy_price
    arbitrage = (spread / (price_std + 0.001)) * (1.0 + self.calculate_regret_urgency(current_grid_buy_price, current_grid_sell_price, current_soc_ratio))

    if current_soc_ratio < target_low:
      soc_pressure = -1.0 + ((current_soc_ratio - target_low) / (target_low + 0.001)) * 0.4
    elif current_soc_ratio > target_high:
      soc_pressure = 1.0 + ((current_soc_ratio - target_high) / (1.0 - target_high)) * 0.4
    else:
      soc_pressure = (current_soc_ratio - 0.5) * 0.2

    pv_pressure = (current_pv_generation_kw - current_demand_kw) / (battery_capacity_kwh + 1.0)

    decision_signal = (
      arbitrage * self.adaptive_weights["arbitrage"] +
      soc_pressure * self.adaptive_weights["soc"] +
      pv_pressure * self.adaptive_weights["pv"]
    )

    power_multiplier = 1.0 + (entropy * 0.3)
    base_charge_power = 8.0 * power_multiplier
    base_discharge_power = 7.0 * power_multiplier

    if decision_signal > 0:
      available = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = (decision_signal ** 0.7) * base_charge_power
      action_kw = min(action_kw, available, base_charge_power * 1.3)

      if current_grid_buy_price < price_avg * 0.95 and current_soc_ratio > 0.8:
        self.regret_tracking["missed_buy"] += 1
    else:
      available = current_energy_stored_kwh
      action_kw = -(abs(decision_signal) ** 0.7) * base_discharge_power
      action_kw = max(action_kw, -available, -base_discharge_power * 1.3)

      if current_grid_sell_price > price_avg * 1.05 and current_soc_ratio < 0.2:
        self.regret_tracking["missed_sell"] += 1

    action_kw = max(-7.5 * power_multiplier, min(8.5 * power_multiplier, action_kw))
    new_soc = current_energy_stored_kwh + action_kw
    if new_soc > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_soc < 0:
      action_kw = -current_energy_stored_kwh

    if len(self.action_history) % 20 == 0:
      self.regret_tracking = {"missed_buy": 0, "missed_sell": 0}

    self.action_history.append(action_kw)
    return action_kw
