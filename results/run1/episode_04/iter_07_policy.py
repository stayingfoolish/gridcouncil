class Policy:
  def __init__(self):
    self.price_history = []
    self.action_history = []
    self.idle_steps = 0

    self.ENSEMBLE_MOMENTUM_WEIGHT = 0.40
    self.ENSEMBLE_MEANREVERSION_WEIGHT = 0.40
    self.ENSEMBLE_SEASONAL_WEIGHT = 0.20
    self.FORECAST_HORIZON = 6

    self.VALUE_FUTURE_STEPS = 6
    self.VALUE_DISCOUNT_FACTOR = 0.95

    self.VOLATILITY_HIGH_THRESHOLD = 0.15
    self.VOLATILITY_LOW_THRESHOLD = 0.05
    self.HIGH_VOL_CHARGE_THRESHOLD = -0.3
    self.HIGH_VOL_DISCHARGE_THRESHOLD = 0.7
    self.LOW_VOL_CHARGE_THRESHOLD = -1.0
    self.LOW_VOL_DISCHARGE_THRESHOLD = 1.0

    self.STRONG_TREND_THRESHOLD = 0.02
    self.TREND_MODE_CHARGE_THRESHOLD = -0.2

    self.URGENCY_PERCENTILE_CRITICAL = 5
    self.URGENCY_PERCENTILE_MODERATE = 25
    self.PRICE_LOOKBACK_HOURS = 72

    self.STAGNATION_THRESHOLD_HOURS = 3
    self.EXPLORATION_CHARGE_RATE = 2.0
    self.EXPLORATION_DISCHARGE_RATE = 1.5

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.PRICE_LOOKBACK_HOURS + 24:
      self.price_history.pop(0)

    if len(self.price_history) < 2:
      return 0.0

    recent_prices = self.price_history[-24:] if len(self.price_history) >= 24 else self.price_history
    mean_price = sum(recent_prices) / len(recent_prices)

    if mean_price == 0:
      return 0.0

    price_std = (sum((p - mean_price) ** 2 for p in recent_prices) / len(recent_prices)) ** 0.5

    velocity = self.price_history[-1] - self.price_history[-2] if len(self.price_history) >= 2 else 0.0

    volatility_ratio = price_std / mean_price if mean_price != 0 else 0

    if volatility_ratio > self.VOLATILITY_HIGH_THRESHOLD:
      charge_threshold = self.HIGH_VOL_CHARGE_THRESHOLD
      discharge_threshold = self.HIGH_VOL_DISCHARGE_THRESHOLD
    elif volatility_ratio < self.VOLATILITY_LOW_THRESHOLD:
      charge_threshold = self.LOW_VOL_CHARGE_THRESHOLD
      discharge_threshold = self.LOW_VOL_DISCHARGE_THRESHOLD
    else:
      charge_threshold = -0.5
      discharge_threshold = 0.5

    if abs(velocity) > self.STRONG_TREND_THRESHOLD:
      charge_threshold = self.TREND_MODE_CHARGE_THRESHOLD

    price_zscore = (current_grid_buy_price - mean_price) / price_std if price_std > 0 else 0

    forecast_prices = []
    for step in range(1, self.VALUE_FUTURE_STEPS + 1):
      decay = 0.9 ** step
      momentum_forecast = current_grid_buy_price + velocity * decay

      meanrev_forecast = (current_grid_buy_price + mean_price) / 2

      seasonal_forecast = mean_price
      if len(self.price_history) >= 168:
        hour_pattern_idx = len(self.price_history) % 24
        seasonal_prices = [self.price_history[i] for i in range(hour_pattern_idx, len(self.price_history), 24)]
        if seasonal_prices:
          seasonal_forecast = sum(seasonal_prices) / len(seasonal_prices)

      ensemble_price = (
        self.ENSEMBLE_MOMENTUM_WEIGHT * momentum_forecast +
        self.ENSEMBLE_MEANREVERSION_WEIGHT * meanrev_forecast +
        self.ENSEMBLE_SEASONAL_WEIGHT * seasonal_forecast
      )
      forecast_prices.append(ensemble_price)

    lookback_start = max(0, len(self.price_history) - self.PRICE_LOOKBACK_HOURS)
    lookback_prices = self.price_history[lookback_start:]

    if lookback_prices:
      sorted_prices = sorted(lookback_prices)
      percentile_5_idx = max(0, len(sorted_prices) * self.URGENCY_PERCENTILE_CRITICAL // 100)
      percentile_25_idx = max(0, len(sorted_prices) * self.URGENCY_PERCENTILE_MODERATE // 100)
      percentile_5 = sorted_prices[percentile_5_idx]
      percentile_25 = sorted_prices[percentile_25_idx]

      if current_grid_buy_price <= percentile_5:
        urgency = 1.0
      elif current_grid_buy_price <= percentile_25:
        urgency = 0.5
      else:
        urgency = 0.2
    else:
      urgency = 0.5

    charge_value = 0.0
    discharge_value = 0.0

    battery_headroom = battery_capacity_kwh - current_energy_stored_kwh

    if battery_headroom > 0 and price_zscore < 1.0:
      simulated_battery = current_energy_stored_kwh
      for step, future_price in enumerate(forecast_prices):
        discount = self.VALUE_DISCOUNT_FACTOR ** step
        if simulated_battery < battery_capacity_kwh:
          charge_value += (future_price - current_grid_buy_price) * discount
          simulated_battery = min(simulated_battery + 1.0, battery_capacity_kwh)

    if current_energy_stored_kwh > 0 and price_zscore > discharge_threshold:
      simulated_battery = current_energy_stored_kwh
      for step, future_price in enumerate(forecast_prices):
        discount = self.VALUE_DISCOUNT_FACTOR ** step
        if simulated_battery > 0:
          discharge_value += (current_grid_buy_price - future_price) * discount
          simulated_battery = max(simulated_battery - 1.0, 0)

    hold_value = 0

    if charge_value > hold_value and charge_value > discharge_value:
      action_kw = 10.0 * (0.3 + 0.7 * urgency)
      action_kw = min(action_kw, 10.0)
      if battery_headroom < action_kw:
        action_kw = battery_headroom
      self.idle_steps = 0
    elif discharge_value > hold_value and discharge_value > charge_value:
      action_kw = -5.0 * (0.3 + 0.7 * urgency)
      action_kw = max(action_kw, -5.0)
      if current_energy_stored_kwh < abs(action_kw):
        action_kw = -current_energy_stored_kwh
      self.idle_steps = 0
    else:
      action_kw = 0.0
      self.idle_steps += 1

    if self.idle_steps >= self.STAGNATION_THRESHOLD_HOURS and abs(velocity) < 0.01:
      if current_energy_stored_kwh < 0.4 * battery_capacity_kwh:
        action_kw = self.EXPLORATION_CHARGE_RATE
        self.idle_steps = 0
      elif current_energy_stored_kwh > 0.6 * battery_capacity_kwh:
        action_kw = -self.EXPLORATION_DISCHARGE_RATE
        self.idle_steps = 0

    action_kw = max(-5.0, min(10.0, action_kw))

    self.action_history.append(action_kw)
    if len(self.action_history) > 100:
      self.action_history.pop(0)

    return action_kw
