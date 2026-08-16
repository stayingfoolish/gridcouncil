class DispatchPolicy:
  def __init__(self):
    """Regime-aware dispatch with temporal clustering and compute batching."""
    self.price_history = []
    self.max_history = 336  # 14 days for stronger patterns
    self.lookback_hours = 72  # 3 days for regime estimation
    self.batch_window = 24  # Batch compute into 24-hour clusters
    self.regime_thresholds = None  # Computed dynamically
    self.backlog_queued_for_regime = {}  # Track compute waiting for low-regime windows

  def _compute_regime_thresholds(self):
    """Classify price distribution into 4 regimes using percentiles."""
    if len(self.price_history) < self.lookback_hours:
      return [30, 50, 75, 100]  # Fallback: [low_p30, medium_p50, peak_p75, spike]

    recent = self.price_history[-self.lookback_hours:]
    recent_sorted = sorted(recent)
    p25 = recent_sorted[len(recent_sorted) // 4]
    p50 = recent_sorted[len(recent_sorted) // 2]
    p75 = recent_sorted[3 * len(recent_sorted) // 4]

    return [p25, p50, p75]  # [low_threshold, medium_threshold, peak_threshold]

  def _classify_regime(self, current_price, thresholds):
    """Return regime: 0=low, 1=medium, 2=peak, 3=spike."""
    if current_price <= thresholds[0]:
      return 0
    elif current_price <= thresholds[1]:
      return 1
    elif current_price <= thresholds[2]:
      return 2
    else:
      return 3

  def _estimate_next_24h_regimes(self, current_price, mean, std_dev):
    """Simulate 24-hour forward prices to identify low-regime windows."""
    path = [current_price]
    mean_reversion_speed = 0.30

    for hour in range(1, 24):
      # Stronger mean-reversion + seasonal dampening
      drift = mean_reversion_speed * (mean - path[-1])
      # Reduce shock noise for cleaner regime prediction
      shock = std_dev * 0.3 * (1 if hour % 6 < 3 else -1)
      next_price = path[-1] + drift + shock
      path.append(max(15.0, next_price))

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
    """Regime-aware batching with aggressive low-price compute servicing."""
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Compute regime thresholds and current regime
    thresholds = self._compute_regime_thresholds()
    current_regime = self._classify_regime(current_price, thresholds)

    if len(self.price_history) >= self.lookback_hours:
      recent = self.price_history[-self.lookback_hours:]
      mean = sum(recent) / len(recent)
      std_dev = (sum((p - mean) ** 2 for p in recent) / len(recent)) ** 0.5
    else:
      mean, std_dev = 50.0, 15.0

    forward_prices = self._estimate_next_24h_regimes(current_price, mean, std_dev)

    # BATTERY STRATEGY: Reserve for peak avoidance (regimes 2-3)
    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0.5

    if current_regime >= 2 and soc_ratio > 0.20:
      # Peak/spike: discharge aggressively to absorb load
      available_discharge = min(battery_soc_mwh * 0.9, battery_power_mw * 1.5)
      battery_mw = -available_discharge
    elif current_regime == 0 and soc_ratio < 0.80:
      # Low-price regime: charge to prepare for peaks
      available_charge = min(battery_capacity_mwh - battery_soc_mwh, battery_power_mw * 1.3)
      battery_mw = available_charge
    else:
      battery_mw = 0.0

    # BACKLOG SERVING: Aggressive deferral to regime-0 windows; strict deadline enforcement
    deadline_urgency = 0.0
    if oldest_backlog_age_h >= 20:
      deadline_urgency = min((oldest_backlog_age_h - 20) / 4.0, 1.0)  # Stricter: 20h threshold

    flex_serve_mw = arriving_flex_mw  # Always serve arriving (hard deadline is short)

    if current_regime == 0 and backlog_mwh > 0 and deadline_urgency < 0.5:
      # AGGRESSIVE: Serve all backlog when in low-price regime
      flex_serve_mw += backlog_mwh * 0.85
    elif current_regime == 1 and backlog_mwh > 0 and deadline_urgency < 0.3:
      # Medium regime: partial serve (let more accumulate for regime-0)
      flex_serve_mw += backlog_mwh * 0.30
    elif deadline_urgency >= 0.5:
      # MUST SERVE: Deadline imminent
      serve_fraction = 0.60 + deadline_urgency * 0.40
      flex_serve_mw += backlog_mwh * serve_fraction

    # Fallback: never exceed 2x arriving to prevent runaway serving
    flex_serve_mw = min(flex_serve_mw, arriving_flex_mw + 2.5 * backlog_mwh)

    return flex_serve_mw, battery_mw
