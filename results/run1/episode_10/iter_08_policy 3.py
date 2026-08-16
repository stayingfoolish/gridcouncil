class Policy:
  def __init__(self):
    self.price_history = []
    self.volatility_window = 12
    self.price_momentum_window = 3
    self.price_acceleration_threshold = 0.15

    self.aggressive_buy_zscore = -0.5
    self.conservative_sell_zscore = 0.9
    self.opportunistic_soc_low = 0.25
    self.opportunistic_soc_high = 0.80

    self.high_volatility_charge = 10.0
    self.high_volatility_discharge = 9.0
    self.low_volatility_charge = 5.0
    self.low_volatility_discharge = 4.5

    self.acceleration_charge_boost = 2.0
    self.acceleration_discharge_boost = 1.8

    self.low_soc_penalty = 0.6
    self.high_soc_penalty = 0.4

  def _calculate_statistics(self):
    if len(self.price_history) < 2:
      return 0, 1, 0, 0

    mean = sum(self.price_history) / len(self.price_history)
    variance = sum((x - mean) ** 2 for x in self.price_history) / len(self.price_history)
    std = variance ** 0.5 if variance > 0 else 1

    current_price = self.price_history[-1]
    zscore = (current_price - mean) / std if std > 0 else 0

    momentum = 0
    if len(self.price_history) >= 2:
      momentum = self.price_history[-1] - self.price_history[-2]

    return mean, std, zscore, momentum

  def _detect_price_regime(self):
    if len(self.price_history) < self.volatility_window:
      return 'medium'

    recent_prices = self.price_history[-self.volatility_window:]
    recent_mean = sum(recent_prices) / len(recent_prices)
    recent_std = (sum((x - recent_mean) ** 2 for x in recent_prices) / len(recent_prices)) ** 0.5

    if len(self.price_history) >= 50:
      historical_prices = self.price_history[-50:]
      historical_mean = sum(historical_prices) / len(historical_prices)
      historical_std = (sum((x - historical_mean) ** 2 for x in historical_prices) / len(historical_prices)) ** 0.5
    else:
      historical_std = recent_std

    if historical_std == 0:
      historical_std = 1

    if recent_std > historical_std * 1.3:
      return 'high'
    elif recent_std > historical_std * 0.8:
      return 'medium'
    return 'low'

  def _detect_price_acceleration(self):
    if len(self.price_history) < 2 * self.price_momentum_window + 1:
      return 0

    recent_momentum = self.price_history[-1] - self.price_history[-self.price_momentum_window]
    prior_momentum = self.price_history[-self.price_momentum_window-1] - self.price_history[-2*self.price_momentum_window-1]

    accel = recent_momentum - prior_momentum
    if accel < -self.price_acceleration_threshold:
      return -1
    elif accel > self.price_acceleration_threshold:
      return 1
    return 0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    self.price_history.append(current_grid_buy_price)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    regime = self._detect_price_regime()
    acceleration = self._detect_price_acceleration()

    if regime == 'high':
      max_charge = self.high_volatility_charge
      max_discharge = self.high_volatility_discharge
    elif regime == 'low':
      max_charge = self.low_volatility_charge
      max_discharge = self.low_volatility_discharge
    else:
      max_charge = (self.high_volatility_charge + self.low_volatility_charge) / 2
      max_discharge = (self.high_volatility_discharge + self.low_volatility_discharge) / 2

    if current_demand_kw > current_pv_generation_kw + current_energy_stored_kwh / 0.25:
      deficit = current_demand_kw - (current_pv_generation_kw + current_energy_stored_kwh / 0.25)
      if deficit > 0:
        action_kw = min(max_charge, deficit)
        return max(-max_discharge, min(max_charge, action_kw))

    mean, std, zscore, momentum = self._calculate_statistics()

    action_kw = 0.0

    if zscore < self.aggressive_buy_zscore and current_soc < 0.90:
      action_kw = max_charge * 0.9
      if acceleration == -1:
        action_kw = max_charge * self.acceleration_charge_boost
      elif momentum < 0:
        action_kw = max_charge

    elif zscore > self.conservative_sell_zscore and current_soc > 0.25:
      action_kw = -max_discharge * 0.8
      if acceleration == 1:
        action_kw = -max_discharge * self.acceleration_discharge_boost
      elif momentum > 0:
        action_kw = -max_discharge

    elif current_soc < self.opportunistic_soc_low and current_grid_buy_price < mean * 0.97:
      action_kw = max_charge * 0.8
    elif current_soc > self.opportunistic_soc_high and current_grid_sell_price > mean * 1.03:
      action_kw = -max_discharge * 0.7

    max_charge_rate = min(max_charge, (battery_capacity_kwh - current_energy_stored_kwh) / 0.25)
    max_discharge_rate = min(max_discharge, current_energy_stored_kwh / 0.25)

    action_kw = max(-max_discharge_rate, min(max_charge_rate, action_kw))

    return action_kw
