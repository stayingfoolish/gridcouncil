class Policy:
  def __init__(self):
    self.price_history = []
    self.cycle_pattern_window = 24
    self.cycle_confidence_threshold = 3  # std deviations

    # Aggressive SOC management
    self.min_discharge_soc = 0.15  # Allow deeper discharge
    self.max_charge_soc = 0.95    # Use almost full capacity
    self.cycle_target_soc = 0.50  # Sweet spot for cycling

    # Mode-specific parameters
    self.accumulation_rate = 12.0  # Max charge in low-price mode
    self.extraction_rate = 11.0    # Max discharge in high-price mode
    self.holding_rate = 6.0        # Conservative rate in neutral mode

    # Cycle detection
    self.valley_threshold_percentile = 0.25
    self.peak_threshold_percentile = 0.75
    self.price_trend_slope = 0.0
    self.price_trend_confidence = 0.0

    # Counters for cycle tracking
    self.hours_in_cycle = 0
    self.mode = 'HOLDING'

  def _detect_daily_cycle(self):
    if len(self.price_history) < self.cycle_pattern_window:
      return 'HOLDING', 0.0, 0.0

    recent_prices = self.price_history[-self.cycle_pattern_window:]
    mean = sum(recent_prices) / len(recent_prices)
    std = (sum((x - mean) ** 2 for x in recent_prices) / len(recent_prices)) ** 0.5 or 1

    current_price = recent_prices[-1]

    # Identify valley: bottom quartile
    sorted_prices = sorted(recent_prices)
    valley_threshold = sorted_prices[len(sorted_prices) // 4]

    # Identify peak: top quartile
    peak_threshold = sorted_prices[3 * len(sorted_prices) // 4]

    # Compute trend using last 4 prices
    if len(recent_prices) >= 4:
      recent_trend = (recent_prices[-1] - recent_prices[-4]) / 4
    else:
      recent_trend = 0

    if current_price <= valley_threshold * 1.05:
      return 'ACCUMULATION', valley_threshold, peak_threshold
    elif current_price >= peak_threshold * 0.95:
      return 'EXTRACTION', valley_threshold, peak_threshold
    else:
      return 'HOLDING', valley_threshold, peak_threshold

  def _predict_next_trend(self):
    """Simple linear regression on last 6 hours"""
    if len(self.price_history) < 6:
      return 0.0  # No trend

    prices = self.price_history[-6:]
    n = len(prices)
    mean_x = n / 2
    mean_y = sum(prices) / n

    numerator = sum((i - mean_x) * (prices[i] - mean_y) for i in range(n))
    denominator = sum((i - mean_x) ** 2 for i in range(n))

    slope = numerator / denominator if denominator > 0 else 0
    return slope

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

    # Detect operating mode based on daily cycle
    mode, valley_price, peak_price = self._detect_daily_cycle()
    self.mode = mode

    # Predict price trend
    trend_slope = self._predict_next_trend()

    # Base action on mode and SOC
    action_kw = 0.0

    # CRITICAL: Supply demand constraint (highest priority)
    supply_capacity = current_pv_generation_kw + (current_energy_stored_kwh / 0.25)
    if current_demand_kw > supply_capacity:
      deficit = current_demand_kw - supply_capacity
      action_kw = min(self.extraction_rate, deficit)
      return max(-self.extraction_rate, min(self.accumulation_rate, action_kw))

    if mode == 'ACCUMULATION':
      # Price is low - charge aggressively unless at capacity
      if current_soc < self.max_charge_soc:
        action_kw = self.accumulation_rate
        # Slightly reduce if we're already at good SOC for cycling
        if current_soc > self.cycle_target_soc:
          action_kw = self.accumulation_rate * 0.7
      # Even more aggressive if trend predicts prices staying low
      if trend_slope < -0.01:
        action_kw = self.accumulation_rate * 1.1

    elif mode == 'EXTRACTION':
      # Price is high - discharge aggressively unless depleted
      if current_soc > self.min_discharge_soc:
        action_kw = -self.extraction_rate
        # Reduce discharge if SOC is getting critically low
        if current_soc < self.cycle_target_soc:
          action_kw = -self.extraction_rate * 0.6
      # More aggressive if trend predicts prices staying high
      if trend_slope > 0.01:
        action_kw = -self.extraction_rate * 1.1

    else:  # HOLDING mode
      # Neutral - optimize position for next cycle
      # If SOC is too low, gently charge
      if current_soc < self.cycle_target_soc - 0.1:
        action_kw = self.holding_rate * 0.5
      # If SOC is too high, gently discharge
      elif current_soc > self.cycle_target_soc + 0.1:
        action_kw = -self.holding_rate * 0.5

    # Apply physical constraints
    max_charge_rate = min(self.accumulation_rate, (battery_capacity_kwh - current_energy_stored_kwh) / 0.25)
    max_discharge_rate = min(self.extraction_rate, current_energy_stored_kwh / 0.25)

    action_kw = max(-max_discharge_rate, min(max_charge_rate, action_kw))

    return action_kw
