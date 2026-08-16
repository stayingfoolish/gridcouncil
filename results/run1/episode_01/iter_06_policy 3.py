class Policy:
  def __init__(self):
    """Multi-regime adaptive battery management"""
    self.price_history = []
    self.max_history = 60  # Extended for better cycle detection

    # Regime detection parameters
    self.long_ma = 20      # Long-term average for trend
    self.mid_ma = 8        # Medium-term for volatility baseline
    self.acceleration_window = 5

    # Regime thresholds
    self.trend_threshold = 0.08  # Price change rate to confirm trend
    self.mean_revert_threshold = 0.12  # Std dev ratio for mean reversion

    # Adaptive policy parameters (updated per regime)
    self.current_regime = "neutral"
    self.regime_confidence = 0.0

  def classify_market_regime(self):
    """Detect which market state we're in with confidence score"""
    if len(self.price_history) < self.mid_ma:
      return "neutral", 0.0

    recent = self.price_history[-self.mid_ma:]
    long_term = self.price_history[-self.long_ma:]

    recent_mean = sum(recent) / len(recent)
    long_mean = sum(long_term) / len(long_term)

    # Calculate acceleration (second derivative of price)
    if len(self.price_history) >= self.acceleration_window + 1:
      accel_window = self.price_history[-(self.acceleration_window+1):]
      velocity = [(accel_window[i+1] - accel_window[i]) for i in range(len(accel_window)-1)]
      acceleration = velocity[-1] - velocity[0]
    else:
      acceleration = 0.0

    # Trend detection
    trend_rate = (recent_mean - long_mean) / (long_mean + 0.01)

    # Volatility-normalized std dev for mean reversion signal
    volatility = (sum((p - recent_mean)**2 for p in recent) / len(recent))**0.5
    volatility_ratio = volatility / (recent_mean + 0.01)

    # Regime classification with confidence
    if trend_rate > self.trend_threshold and acceleration > 0:
      return "accumulation", min(0.95, abs(trend_rate) * 3)  # Prices falling, accelerating down
    elif trend_rate < -self.trend_threshold and acceleration < 0:
      return "distribution", min(0.95, abs(trend_rate) * 3)  # Prices rising, accelerating up
    elif volatility_ratio > self.mean_revert_threshold and abs(trend_rate) < 0.05:
      return "mean_reversion", min(0.90, volatility_ratio * 2)
    else:
      return "neutral", 0.4

  def get_adaptive_soc_target(self, regime, confidence):
    """Dynamic SOC targets based on market regime"""
    if confidence < 0.5:
      return 0.50  # Safe middle ground

    regime_targets = {
      "accumulation": 0.20,      # Stay low to buy at bottom
      "distribution": 0.80,      # Stay high to sell at peak
      "mean_reversion": 0.50,    # Neutral for oscillation trading
      "neutral": 0.50
    }
    return regime_targets.get(regime, 0.50)

  def calculate_position_strength(self):
    """How extreme is the current price vs recent history?"""
    if len(self.price_history) < 10:
      return 0.0
    recent = self.price_history[-10:]
    min_price = min(recent)
    max_price = max(recent)
    current = self.price_history[-1]
    if max_price == min_price:
      return 0.0
    return (current - min_price) / (max_price - min_price) * 2 - 1  # Range [-1, 1]

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute regime-aware control strategy"""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Detect market regime
    regime, confidence = self.classify_market_regime()
    self.current_regime = regime
    self.regime_confidence = confidence

    # Get adaptive target SOC
    target_soc = self.get_adaptive_soc_target(regime, confidence)

    # Tighter bands when confident, wider when uncertain
    base_band = 0.03 + (1 - confidence) * 0.07
    soc_band = base_band

    soc_gap = target_soc - current_soc
    action_kw = 0.0

    # Position strength tells us if we're at extremes
    position_strength = self.calculate_position_strength()

    # Regime-specific charge/discharge aggressiveness
    if regime == "accumulation":
      charge_aggression = 1.8  # Charge hard at low prices
      discharge_aggression = 0.3
    elif regime == "distribution":
      charge_aggression = 0.3
      discharge_aggression = 1.8  # Discharge hard at high prices
    elif regime == "mean_reversion":
      charge_aggression = 1.0
      discharge_aggression = 1.0
    else:
      charge_aggression = 0.7
      discharge_aggression = 0.7

    # Main control loop
    if soc_gap > soc_band:
      # Need to charge
      error_magnitude = min(soc_gap / 0.25, 1.0)
      max_charge_rate = battery_capacity_kwh * 0.40
      # Reduce charge at peaks (positive position_strength)
      regime_factor = charge_aggression * (1 - max(0, position_strength) * 0.5)
      charge_power = min(16.0 * error_magnitude * regime_factor, max_charge_rate)
      action_kw = charge_power

    elif soc_gap < -soc_band:
      # Need to discharge
      error_magnitude = min(-soc_gap / 0.25, 1.0)
      max_discharge_rate = battery_capacity_kwh * 0.40
      # Boost discharge at peaks
      regime_factor = discharge_aggression * (1 + max(0, position_strength) * 0.5)
      discharge_power = min(16.0 * error_magnitude * regime_factor, max_discharge_rate)
      action_kw = -discharge_power

    else:
      # Within band - opportunistic trading
      net_power = current_pv_generation_kw - current_demand_kw

      if regime == "accumulation" and net_power > 0.2:
        # Charge excess at low prices
        action_kw = min(net_power * 0.6, 12.0)
      elif regime == "distribution" and net_power < -0.2:
        # Discharge to meet demand at high prices
        action_kw = max(net_power * 0.6, -12.0)
      elif regime == "mean_reversion":
        # Follow price extremes in mean reversion
        if position_strength > 0.3:  # At peak
          action_kw = max(net_power * 0.4, -10.0)
        elif position_strength < -0.3:  # At trough
          action_kw = min(net_power * 0.4, 10.0)

    # Enforce physical limits
    max_charge = battery_capacity_kwh - current_energy_stored_kwh
    max_discharge = current_energy_stored_kwh

    if action_kw > 0:
      action_kw = min(action_kw, max_charge)
    else:
      action_kw = max(action_kw, -max_discharge)

    return action_kw
