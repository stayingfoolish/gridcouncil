class DispatchPolicy:
  def __init__(self):
    """Predictive 6-hour lookahead with mean-reversion and volatility-driven arbitrage."""
    self.price_history = []
    self.max_history = 168  # 7 days
    self.lookback_hours = 24
    self.dispatch_window = 6

  def _compute_price_regime(self):
    """Estimate price distribution statistics."""
    if len(self.price_history) < self.lookback_hours:
      return 50.0, 10.0, 0.5

    recent = self.price_history[-self.lookback_hours:]
    mean = sum(recent) / len(recent)
    variance = sum((p - mean) ** 2 for p in recent) / len(recent)
    std_dev = variance ** 0.5
    hour_of_week = (len(self.price_history) % 168)
    day_of_week = hour_of_week // 24
    hour_in_day = hour_of_week % 24

    # Weekly/daily seasonality adjustment
    if day_of_week >= 5:  # Weekend
      seasonal_factor = 0.85
    elif hour_in_day in [6, 7, 17, 18, 19, 20]:  # Peak hours
      seasonal_factor = 1.25
    else:
      seasonal_factor = 1.0

    return mean * seasonal_factor, std_dev, min(0.9, std_dev / max(mean, 1.0))

  def _estimate_price_path(self, current_price, mean, std_dev, volatility_regime):
    """Estimate 6-hour forward price path with mean reversion."""
    path = [current_price]
    mean_reversion_speed = 0.25 + volatility_regime * 0.15

    for hour in range(1, self.dispatch_window):
      # Mean reversion with volatility scaling
      drift = mean_reversion_speed * (mean - path[-1])
      shock = std_dev * (volatility_regime ** 0.5) * 0.5 * (hour % 2)
      next_price = path[-1] + drift + shock
      path.append(max(10.0, next_price))  # Floor at $10/MWh

    return path

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
    """Decision logic using predictive lookahead and aggressive arbitrage."""
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Compute regime
    mean, std_dev, volatility = self._compute_price_regime()
    price_path = self._estimate_price_path(current_price, mean, std_dev, volatility)
    max_forward_price = max(price_path)
    min_forward_price = min(price_path)
    price_percentile = (current_price - min_forward_price) / max(max_forward_price - min_forward_price, 1.0)

    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0.5

    # AGGRESSIVE DISCHARGE: Discharge when in top 30% of expected prices
    aggressive_discharge_threshold = mean + std_dev * 0.8
    if current_price >= aggressive_discharge_threshold and soc_ratio > 0.15:
      available_discharge = min(battery_soc_mwh, battery_power_mw * 1.2)  # Slight overdischarge allowed
      battery_mw = -available_discharge
    # OPPORTUNISTIC CHARGE: Charge when below mean - volatility and we have headroom
    elif current_price <= (mean - std_dev * 0.4) and soc_ratio < 0.75:
      available_charge = min(battery_capacity_mwh - battery_soc_mwh, battery_power_mw * 1.2)
      battery_mw = available_charge
    else:
      battery_mw = 0.0

    # INTELLIGENT BACKLOG SERVING: Only defer when forward prices are clearly higher
    avg_forward_price = sum(price_path) / len(price_path)
    deadline_urgency = 0.0
    if oldest_backlog_age_h >= 18:  # Stricter deadline (was 15)
      deadline_urgency = min((oldest_backlog_age_h - 18) / 8.0, 1.0)

    cost_of_deferral = (avg_forward_price - current_price) * backlog_mwh / 24.0
    benefit_threshold = 500.0  # Only defer if expected savings > $500/hour

    if cost_of_deferral > benefit_threshold and deadline_urgency == 0.0:
      # Defer - price expected to drop
      flex_serve_mw = arriving_flex_mw
    elif current_price < mean * 0.90:
      # Serve aggressively when price is low
      serve_fraction = 0.65 + deadline_urgency * 0.35
      backlog_to_serve = backlog_mwh * serve_fraction
      flex_serve_mw = arriving_flex_mw + backlog_to_serve
    elif deadline_urgency > 0.6:
      # Must serve due to deadline
      backlog_to_serve = backlog_mwh * (0.5 + deadline_urgency * 0.5)
      flex_serve_mw = arriving_flex_mw + backlog_to_serve
    else:
      flex_serve_mw = arriving_flex_mw

    return flex_serve_mw, battery_mw
