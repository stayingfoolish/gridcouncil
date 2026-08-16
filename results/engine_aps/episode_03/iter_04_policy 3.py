class DispatchPolicy:
  def __init__(self):
    self.price_history = []
    self.flex_load_history = []
    self.backlog_history = []
    self.volatility_window = 72
    self.trend_window = 48
    self.planning_horizon = 48
    self.arbitrage_soc_target = 0.70
    self.hedge_reserve = 0.20
    self.battery_charge_rate = 8
    self.regime = None
    self.drift_estimate = 0.0
    self.volatility_estimate = 15.0
    self.serve_tiers = [0.9, 0.7, 0.5, 0.2]
    self.batch_flexibility = 4
    self.min_urgency_serve = 0.05

  def take_action(self,
    hour_of_day: int,
    current_price: float,
    firm_load_mw: float,
    arriving_flex_mw: float,
    backlog_mwh: float,
    oldest_backlog_age_h: float,
    battery_soc_mwh: float,
    battery_capacity_mwh: float,
    battery_power_mw: float,
  ) -> tuple:
    self.price_history.append(current_price)
    self.flex_load_history.append(arriving_flex_mw)
    self.backlog_history.append(backlog_mwh)

    for hist in [self.price_history, self.flex_load_history, self.backlog_history]:
      if len(hist) > 240:
        hist.pop(0)

    if len(self.price_history) >= self.volatility_window:
      recent_prices = self.price_history[-self.trend_window:]
      volatility = self._compute_volatility(recent_prices)
      drift = self._compute_drift(recent_prices)
      self.volatility_estimate = 0.8 * self.volatility_estimate + 0.2 * volatility
      self.drift_estimate = 0.8 * self.drift_estimate + 0.2 * drift

      if self.drift_estimate > 2.0:
        self.regime = 'trending_up'
      elif self.drift_estimate < -2.0:
        self.regime = 'trending_down'
      elif self.volatility_estimate > 25.0:
        self.regime = 'volatile'
      else:
        self.regime = 'stable'

    predicted_prices = self._forecast_prices_regime_aware(self.planning_horizon)
    arbitrage_windows = self._find_arbitrage_windows(predicted_prices)
    next_cheap_window = arbitrage_windows[0] if arbitrage_windows else None
    next_peak = self._find_next_peak(predicted_prices)

    soc_ratio = battery_soc_mwh / max(1.0, battery_capacity_mwh)
    battery_mw = 0.0

    if next_cheap_window:
      hours_until_cheap = next_cheap_window['hours_until']
      if hours_until_cheap > 0 and hours_until_cheap <= 6:
        target_soc = self.arbitrage_soc_target * battery_capacity_mwh
        if soc_ratio < self.arbitrage_soc_target and current_price < next_cheap_window['price'] * 1.1:
          charge_amount = min(
            self.battery_charge_rate * 2.0,
            target_soc - battery_soc_mwh
          )
          battery_mw = charge_amount

    if battery_mw == 0.0:
      if next_peak and next_peak['hours_until'] > 2 and next_peak['hours_until'] <= 8:
        if soc_ratio > 0.5:
          discharge_amount = battery_power_mw * 1.2
          available_discharge = battery_soc_mwh * (1.0 - self.hedge_reserve)
          battery_mw = -min(discharge_amount, available_discharge)

    hours_until_deadline = 24.0 - oldest_backlog_age_h
    serve_fraction = 0.0

    if hours_until_deadline < 2:
      serve_fraction = 1.0
    elif hours_until_deadline < 6:
      price_factor = min(1.0, (current_price / (sum(predicted_prices) / len(predicted_prices) if predicted_prices else 60)))
      serve_fraction = 0.6 + 0.35 * (1.0 - price_factor)
    else:
      min_forecast_price = min(predicted_prices) if predicted_prices else current_price
      price_proximity = (current_price - min_forecast_price) / max(1.0, min_forecast_price)

      if price_proximity < 0.05:
        serve_fraction = self.serve_tiers[0]
      elif price_proximity < 0.10:
        serve_fraction = self.serve_tiers[1]
      elif price_proximity < 0.20:
        serve_fraction = self.serve_tiers[2]
      else:
        serve_fraction = self.serve_tiers[3]

    if battery_mw < 0 or (next_peak and next_peak['hours_until'] < 4):
      serve_fraction = min(1.0, serve_fraction * 1.3)

    new_flex_mw = arriving_flex_mw * serve_fraction
    backlog_portion = max(0.0, backlog_mwh / max(1.0, hours_until_deadline)) * 0.8
    flex_serve_mw = min(arriving_flex_mw + backlog_mwh, new_flex_mw + backlog_portion)
    flex_serve_mw = max(flex_serve_mw, self.min_urgency_serve * arriving_flex_mw)

    return flex_serve_mw, battery_mw

  def _compute_volatility(self, prices):
    if len(prices) < 2:
      return 0.0
    changes = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    return sum(changes) / len(changes)

  def _compute_drift(self, prices):
    if len(prices) < 2:
      return 0.0
    recent = prices[-24:]
    if len(recent) < 2:
      return 0.0
    return (recent[-1] - recent[0]) / len(recent)

  def _forecast_prices_regime_aware(self, window_hours):
    if len(self.price_history) < 48:
      return [60.0] * window_hours

    recent_prices = self.price_history[-48:]
    base_forecast = sum(recent_prices) / len(recent_prices)

    if self.regime == 'trending_up':
      adjustment = 1.0 + (self.drift_estimate / base_forecast)
    elif self.regime == 'trending_down':
      adjustment = 1.0 - (abs(self.drift_estimate) / base_forecast)
    elif self.regime == 'volatile':
      adjustment = 1.0
    else:
      adjustment = 1.0

    forecast = []
    for h in range(window_hours):
      hour_of_forecast = (len(self.price_history) + h) % 24
      seasonal_factor = 1.0 + 0.15 * (0.5 if hour_of_forecast in [16, 17, 18, 19] else -0.3)
      predicted = base_forecast * adjustment * seasonal_factor
      forecast.append(predicted)

    return forecast

  def _find_arbitrage_windows(self, predicted_prices):
    windows = []
    min_price = min(predicted_prices) if predicted_prices else 60.0

    for i, price in enumerate(predicted_prices):
      if price <= min_price * 1.05:
        windows.append({
          'hours_until': i,
          'price': price,
          'duration': self._window_duration(predicted_prices, i)
        })

    return sorted(windows, key=lambda w: w['hours_until'])

  def _find_next_peak(self, predicted_prices):
    if len(predicted_prices) < 2:
      return None

    avg_price = sum(predicted_prices) / len(predicted_prices)
    for i, price in enumerate(predicted_prices[1:], 1):
      if price > avg_price * 1.2:
        return {
          'hours_until': i,
          'price': price
        }

    return None

  def _window_duration(self, prices, start_index):
    min_price = min(prices) if prices else 60.0
    duration = 0
    for i in range(start_index, len(prices)):
      if prices[i] <= min_price * 1.08:
        duration += 1
      else:
        break
    return duration
