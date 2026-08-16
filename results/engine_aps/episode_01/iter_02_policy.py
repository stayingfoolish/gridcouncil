class DispatchPolicy:
  def __init__(self):
    self.price_history = []  # Full 336-hour (2-week) history for pattern detection
    self.hourly_averages = [0.0] * 24  # Learned hourly baseline prices
    self.hourly_counts = [0] * 24  # Sample counts per hour
    self.learning_phase = True
    self.episodes_seen = 0

  def _update_hourly_pattern(self, hour_of_day: int, current_price: float):
    """Update rolling hourly average for time-of-day pattern learning"""
    alpha = 0.1  # Exponential smoothing for hourly patterns
    self.hourly_averages[hour_of_day] = (
      alpha * current_price + (1 - alpha) * self.hourly_averages[hour_of_day]
    )

  def _predict_next_hour_price(self, hour_of_day: int, current_price: float) -> float:
    """Forecast next hour's price using hourly patterns and momentum"""
    next_hour = (hour_of_day + 1) % 24
    pattern_forecast = self.hourly_averages[next_hour]

    # Blend current momentum into forecast
    if len(self.price_history) >= 3:
      recent_momentum = (self.price_history[-1] - self.price_history[-3]) / 3
      momentum_factor = 0.3
    else:
      recent_momentum = 0
      momentum_factor = 0

    return pattern_forecast * (1 - momentum_factor) + (current_price + recent_momentum) * momentum_factor

  def _compute_backlog_urgency(self, oldest_backlog_age_h: float, backlog_mwh: float) -> float:
    """Urgency [0-1]: how important it is to serve backlog now vs later"""
    if backlog_mwh == 0:
      return 0.0

    # Hard cutoff at 23h; soft ramp before that
    # At 12h age: urgency = 0.3; at 20h: urgency = 0.8
    urgency = min(1.0, max(0.0, (oldest_backlog_age_h - 6.0) / 14.0))
    return urgency

  def _get_hour_category(self, hour_of_day: int) -> str:
    """Classify hour as peak/shoulder/valley"""
    if 14 <= hour_of_day <= 18:
      return "peak"
    elif 2 <= hour_of_day <= 5:
      return "valley"
    else:
      return "shoulder"

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
    if len(self.price_history) > 336:
      self.price_history.pop(0)

    self._update_hourly_pattern(hour_of_day, current_price)

    next_price = self._predict_next_hour_price(hour_of_day, current_price)
    backlog_urgency = self._compute_backlog_urgency(oldest_backlog_age_h, backlog_mwh)
    hour_category = self._get_hour_category(hour_of_day)
    avg_price_24h = sum(self.price_history[-24:]) / max(1, len(self.price_history[-24:]))

    flex_serve_mw = arriving_flex_mw
    battery_mw = 0.0

    # EMERGENCY: Always clear if >23h old
    if oldest_backlog_age_h >= 23:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh, 250)
      return flex_serve_mw, battery_mw

    # BATTERY CHARGING LOGIC (Adaptive per hour category)
    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0

    if hour_category == "valley":
      # Valley hours: charge aggressively if below 70%
      charge_target = 0.70
      if soc_ratio < charge_target and current_price < avg_price_24h * 0.85:
        available = battery_capacity_mwh - battery_soc_mwh
        battery_mw = min(available, battery_power_mw)

    elif hour_category == "shoulder":
      # Shoulder: selective charging only below 1.0x average
      if soc_ratio < 0.60 and current_price < avg_price_24h * 0.95:
        available = battery_capacity_mwh - battery_soc_mwh
        charge_amount = min(available, battery_power_mw * 0.7)
        battery_mw = charge_amount

    # FLEX DISPATCH LOGIC (Urgency + Price driven)
    if backlog_urgency > 0.5:
      # High urgency: serve backlog + arriving (but consider price)
      if current_price < avg_price_24h * 1.10:
        flex_serve_mw = min(arriving_flex_mw + backlog_mwh * 0.5, 250)
      else:
        flex_serve_mw = arriving_flex_mw  # Serve arriving, hold backlog

    elif current_price > avg_price_24h * 1.25:
      # High price: serve backlog aggressively for arbitrage
      if backlog_mwh > 0:
        flex_serve_mw = min(arriving_flex_mw + backlog_mwh * 0.8, 250)

    # BATTERY DISCHARGE LOGIC (Optimized for peak hours)
    if hour_category == "peak" and battery_soc_mwh > battery_capacity_mwh * 0.25:
      available = min(battery_soc_mwh, battery_power_mw)

      if current_price > avg_price_24h * 1.20:
        # High price: discharge strongly (80%)
        battery_mw = -available * 0.80
      elif current_price > avg_price_24h * 1.10:
        # Moderate high: discharge medium (60%)
        battery_mw = -available * 0.60

    elif hour_category == "shoulder" and current_price > avg_price_24h * 1.15:
      available = min(battery_soc_mwh, battery_power_mw)
      battery_mw = -available * 0.50

    return flex_serve_mw, battery_mw
