class Policy:
  def __init__(self):
    self.price_history = []
    self.extended_history_len = 48
    self.volatility_window = 12

    self.min_soc_target = 0.02
    self.max_soc_target = 0.98

    self.max_charge_power = 15.0
    self.max_discharge_power = 12.0

    self.price_estimate = None
    self.price_variance = 1.0
    self.process_noise = 0.05
    self.measurement_noise = 0.1

    self.horizon_prices = [None] * 3

    self.last_action = 0.0
    self.action_inertia = 0.4

    self.current_volatility = 0.0

  def calculate_volatility(self, price_history):
    if len(price_history) < self.volatility_window:
      return 0.05
    recent = price_history[-self.volatility_window:]
    mean = sum(recent) / len(recent)
    variance = sum((p - mean) ** 2 for p in recent) / len(recent)
    return (variance ** 0.5) / (mean + 0.001)

  def kalman_predict(self, current_price):
    if self.price_estimate is None:
      self.price_estimate = current_price
      return current_price

    predicted = self.price_estimate
    predicted_variance = self.price_variance + self.process_noise

    kalman_gain = predicted_variance / (predicted_variance + self.measurement_noise)
    self.price_estimate = predicted + kalman_gain * (current_price - predicted)
    self.price_variance = (1 - kalman_gain) * predicted_variance

    return self.price_estimate

  def multi_horizon_predict(self, price_history, current_price):
    self.current_volatility = self.calculate_volatility(price_history)

    filtered_price = self.kalman_predict(current_price)

    if len(price_history) < 8:
      self.horizon_prices = [current_price, current_price, current_price]
      return self.horizon_prices

    recent = price_history[-8:]
    trend = (sum(recent[-4:]) - sum(recent[:4])) / (4 * (sum(recent) / len(recent) + 0.001))

    volatility_factor = min(self.current_volatility * 2, 0.4)

    self.horizon_prices[0] = filtered_price + trend * 0.8
    self.horizon_prices[1] = self.horizon_prices[0] + trend * 0.5 * (1 - volatility_factor * 0.5)
    self.horizon_prices[2] = self.horizon_prices[1] + trend * 0.3 * (1 - volatility_factor)

    return self.horizon_prices

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.extended_history_len:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    h1, h2, h3 = self.multi_horizon_predict(self.price_history, current_grid_buy_price)

    action_kw = 0.0

    confidence_multiplier = 1.0 + self.current_volatility * 3.0

    if h1 > current_grid_buy_price and h2 > h1 and h3 > h2:
      margin_h1 = (h1 - current_grid_buy_price) / (current_grid_buy_price + 0.001)
      if margin_h1 > 0.015:
        available = battery_capacity_kwh - current_energy_stored_kwh
        if available > 0.1:
          soc_headroom = (self.max_soc_target - current_soc) / (self.max_soc_target - self.min_soc_target + 0.001)
          power_scaling = 0.7 + confidence_multiplier * 0.35 + soc_headroom * 0.4
          action_kw = min(self.max_charge_power * power_scaling, available)

    elif h1 < current_grid_sell_price and h2 < h1 and h3 < h2:
      margin_h1 = (current_grid_sell_price - h1) / (h1 + 0.001)
      if margin_h1 > 0.015:
        available = current_energy_stored_kwh
        if available > 0.1:
          soc_position = (current_soc - self.min_soc_target) / (self.max_soc_target - self.min_soc_target + 0.001)
          power_scaling = 0.7 + confidence_multiplier * 0.35 + soc_position * 0.4
          action_kw = -min(self.max_discharge_power * power_scaling, available)

    elif (h1 - current_grid_buy_price) > 0.08 and current_soc < 0.7:
      available = battery_capacity_kwh - current_energy_stored_kwh
      if available > 0.1:
        action_kw = min(self.max_charge_power * 0.9, available)

    else:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 0.3 and current_soc < self.max_soc_target - 0.05:
        action_kw = min(pv_excess * 0.85, self.max_charge_power * 0.3,
                       battery_capacity_kwh - current_energy_stored_kwh)
      elif pv_excess < -0.3 and current_soc > self.min_soc_target + 0.05:
        action_kw = -min(abs(pv_excess) * 0.75, self.max_discharge_power * 0.3,
                        current_energy_stored_kwh)

    action_kw = action_kw * (1 - self.action_inertia) + self.last_action * self.action_inertia
    self.last_action = action_kw

    new_soc = current_energy_stored_kwh + action_kw
    if new_soc > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_soc < 0:
      action_kw = -current_energy_stored_kwh

    return action_kw
