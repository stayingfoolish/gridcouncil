class DispatchPolicy:
  def __init__(self):
    """Forward-looking dispatch with 24-hour planning horizon."""
    self.price_history = []
    self.flex_load_history = []
    self.backlog_history = []

    # Planning horizon and batch parameters
    self.planning_hours = 24
    self.price_forecast_window = 12  # Hours ahead we try to predict
    self.batch_serve_window = 3  # Hours to serve batched compute

    # Price thresholds (adapted)
    self.critical_price_percentile = 85  # Discharge trigger
    self.cheap_price_percentile = 25    # Charge trigger

    # Deadline and backlog parameters
    self.hard_deadline_hours = 24
    self.critical_urgency_threshold = 18  # Hours remaining
    self.batching_threshold_mw = 50  # Min flex load to batch

    # Battery positioning parameters
    self.min_soc_for_peak = 0.60  # Target SOC before predicted peaks
    self.max_discharge_fraction = 0.80  # Don't fully drain

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
    """Temporal arbitrage dispatch with predictive battery positioning."""

    # Update histories
    self.price_history.append(current_price)
    self.flex_load_history.append(arriving_flex_mw)
    self.backlog_history.append(backlog_mwh)

    for hist in [self.price_history, self.flex_load_history, self.backlog_history]:
      if len(hist) > 168:
        hist.pop(0)

    # Phase 1: Predict next 12 hours of prices using price autocorrelation
    predicted_prices = self._forecast_prices(self.price_forecast_window)
    future_peak_price = max(predicted_prices) if predicted_prices else current_price
    future_low_price = min(predicted_prices) if predicted_prices else current_price

    # Compute dynamic thresholds from recent history
    if len(self.price_history) >= 48:
      prices_sorted = sorted(self.price_history[-48:])
      critical_price = prices_sorted[int(len(prices_sorted) * self.critical_price_percentile / 100)]
      cheap_price = prices_sorted[int(len(prices_sorted) * self.cheap_price_percentile / 100)]
    else:
      critical_price = 85.0
      cheap_price = 35.0

    # Phase 2: Battery positioning strategy
    soc_ratio = battery_soc_mwh / max(1.0, battery_capacity_mwh)
    hours_to_predicted_peak = self._hours_to_next_peak(predicted_prices)

    battery_mw = 0.0

    # Charging: fill battery before predicted price peaks (look-ahead 6-8 hours)
    if hours_to_predicted_peak <= self.price_forecast_window and hours_to_predicted_peak > 2:
      target_soc = self.min_soc_for_peak * battery_capacity_mwh
      if soc_ratio < self.min_soc_for_peak and current_price < cheap_price * 1.1:
        charge_amount = min(
          battery_power_mw * 1.5,  # Aggressive charging
          target_soc - battery_soc_mwh
        )
        battery_mw = charge_amount

    # Discharging: discharge during high-price windows, OR during backlog urgency
    hours_until_deadline = self.hard_deadline_hours - oldest_backlog_age_h

    if current_price > critical_price * 0.95:  # Current price high
      discharge_amount = battery_power_mw * min(1.0, soc_ratio / 0.3)
      battery_mw = -min(discharge_amount, battery_soc_mwh * self.max_discharge_fraction)
    elif hours_until_deadline < self.critical_urgency_threshold and backlog_mwh > 10:
      # Critical deadline: use battery to service backlog
      discharge_amount = battery_power_mw * 0.7
      battery_mw = -min(discharge_amount, battery_soc_mwh * 0.5)

    # Phase 3: Temporal batching of flexible compute
    # Key insight: batch compute into windows with predicted low prices

    serve_fraction = 0.0

    # Rule 1: Must serve if backlog is reaching deadline
    if hours_until_deadline < 3:
      serve_fraction = 1.0  # Serve all available
    elif hours_until_deadline < self.critical_urgency_threshold:
      # Ramp up urgency: exponential scaling as deadline approaches
      urgency_factor = (self.critical_urgency_threshold - hours_until_deadline) / (self.critical_urgency_threshold - 3)
      serve_fraction = 0.1 + 0.8 * (urgency_factor ** 1.5)
    else:
      # Opportunistic serving: only serve during predicted low-price windows
      if current_price < future_low_price * 1.05:
        serve_fraction = 0.7  # Aggressive serving in cheap windows
      elif current_price < cheap_price:
        serve_fraction = 0.4  # Moderate serving
      else:
        serve_fraction = 0.05  # Minimal serving in expensive periods

    # Calculate flex serve amount with batching consideration
    new_flex_mw = arriving_flex_mw * serve_fraction
    backlog_age_serving = max(0, (backlog_mwh / 24.0) * (urgency_factor if hours_until_deadline < self.critical_urgency_threshold else 0.3))
    flex_serve_mw = new_flex_mw + backlog_age_serving
    flex_serve_mw = max(0, min(flex_serve_mw, arriving_flex_mw + backlog_mwh / 1.0))

    return flex_serve_mw, battery_mw

  def _forecast_prices(self, window_hours):
    """Forecast next N hours of prices using moving average + seasonal patterns."""
    if len(self.price_history) < 24:
      return [60.0] * window_hours

    # Lightweight forecast: weighted average of recent history with daily seasonality
    recent_same_hour_prices = []
    current_hour = len(self.price_history) % 24
    for i in range(len(self.price_history) - 24, -1, -24):
      if i >= 0 and i < len(self.price_history):
        recent_same_hour_prices.append(self.price_history[i])

    seasonal_component = sum(recent_same_hour_prices[-3:]) / max(1, len(recent_same_hour_prices[-3:])) if recent_same_hour_prices else 60.0
    trend = sum(self.price_history[-12:]) / 12

    forecast = []
    for h in range(window_hours):
      hour_of_forecast = (current_hour + h) % 24
      # Blend trend + seasonal
      predicted = 0.6 * trend + 0.4 * seasonal_component
      forecast.append(predicted)

    return forecast

  def _hours_to_next_peak(self, predicted_prices):
    """Find hours until next predicted price peak."""
    if not predicted_prices:
      return 12

    current_price = predicted_prices[0] if predicted_prices else 60
    for h, p in enumerate(predicted_prices[1:], 1):
      if p > current_price * 1.3:  # 30% increase signals peak
        return h

    return 12
