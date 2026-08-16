class Policy:
  def __init__(self):
    """Demand-predictive arbitrage with regime-aware positioning."""
    self.price_history = []
    self.demand_history = []
    self.action_history = []
    self.profit_history = []
    self.max_history = 48

    self.charge_rate_kw = 2.0
    self.discharge_rate_kw = 2.0

    # SOC policy: fixed wider band, less reactivity
    self.soc_min = 0.10
    self.soc_max = 0.90
    self.soc_target = 0.50  # Anchor point, not constraint

    # Demand prediction: learn seasonal patterns
    self.demand_ma_6h = 0.0  # 6-hour moving average
    self.demand_ma_12h = 0.0  # 12-hour moving average
    self.demand_volatility = 0.5

    # Price regime: classify into 3 states
    self.price_regime = "stable"  # "trending_up", "trending_down", "stable", "volatile"
    self.regime_confidence = 0.0

    # Horizon-aware value: multiple decision timescales
    self.immediate_value_weight = 0.3  # Next 1-2 hours
    self.tactical_value_weight = 0.5   # Next 6-12 hours
    self.strategic_value_weight = 0.2  # Longer term positioning

  def classify_price_regime(self, price_history):
    """Classify price pattern into regime for decision context."""
    if len(price_history) < 12:
      return "stable", 0.3

    recent_prices = price_history[-12:]

    # Only use older prices if we have enough history
    if len(price_history) >= 24:
      older_prices = price_history[-24:-12]
    else:
      # Fallback: use first half of history as older period
      mid = len(price_history) // 2
      older_prices = price_history[:mid]

    recent_trend = (recent_prices[-1] - recent_prices[0]) / (recent_prices[0] + 0.001)
    older_trend = (older_prices[-1] - older_prices[0]) / (older_prices[0] + 0.001) if len(older_prices) > 0 else 0.0

    recent_vol = (sum((p - sum(recent_prices)/len(recent_prices))**2 for p in recent_prices) / len(recent_prices)) ** 0.5
    older_avg = sum(older_prices)/len(older_prices) if len(older_prices) > 0 else sum(recent_prices)/len(recent_prices)

    # Simple regime classifier
    if recent_vol > older_avg * 0.15:
      regime, conf = "volatile", 0.8
    elif recent_trend > 0.05 and older_trend > 0.02:
      regime, conf = "trending_up", 0.7
    elif recent_trend < -0.05 and older_trend < -0.02:
      regime, conf = "trending_down", 0.7
    else:
      regime, conf = "stable", 0.6

    return regime, conf

  def predict_demand_spike(self, demand_history):
    """Predict if demand will spike in next 6-12 hours."""
    if len(demand_history) < 24:
      return 0.5, 0.3  # baseline uncertainty

    recent_avg = sum(demand_history[-6:]) / 6
    recent_trend = (demand_history[-1] - demand_history[-12]) / (demand_history[-12] + 0.001)

    # Simple heuristic: if demand increasing and above moving average, expect spike
    recent_peak = max(demand_history[-12:])
    recent_percentile = recent_avg / (recent_peak + 0.001)

    spike_prob = min(1.0, max(0.0, 0.5 + recent_trend * 1.5 - recent_percentile * 0.3))
    uncertainty = 0.4 if len(demand_history) < 36 else 0.25

    return spike_prob, uncertainty

  def compute_multi_horizon_value(self, current_soc, price_regime, demand_spike_prob,
                                   current_price_ratio, recent_price_momentum):
    """Compute action value across multiple decision horizons."""

    # Immediate arbitrage (spread chasing)
    immediate = 0.0
    if current_price_ratio > 1.08:  # Good discharge opportunity
      immediate += 1.0 * (current_price_ratio - 1.08)
    if current_price_ratio < 0.98:  # Good charge opportunity
      immediate += 1.0 * (0.98 - current_price_ratio)

    # Tactical positioning (6-12 hour horizon)
    tactical = 0.0
    if price_regime == "trending_up":
      # Prices rising: be lower SOC to buy cheaper, sell later
      tactical -= (current_soc - 0.45) * 0.5
      tactical += 0.3  # Bonus to discharge
    elif price_regime == "trending_down":
      # Prices falling: be higher SOC for potential discounted charging
      tactical += (0.55 - current_soc) * 0.5
      tactical += 0.2  # Bonus to charge

    # Strategic positioning (demand prediction)
    strategic = 0.0
    if demand_spike_prob > 0.6 and current_soc < 0.70:
      # Build reserves for predicted demand peak
      strategic += (0.70 - current_soc) * 0.4
      strategic += 0.25  # Bonus to charge

    # Combine horizons with exponential decay
    total_value = (self.immediate_value_weight * immediate +
                   self.tactical_value_weight * tactical +
                   self.strategic_value_weight * strategic)

    return total_value

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute demand-predictive arbitrage strategy."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    action_kw = 0.0

    # Update demand moving averages
    if len(self.demand_history) >= 6:
      self.demand_ma_6h = sum(self.demand_history[-6:]) / 6
    if len(self.demand_history) >= 12:
      self.demand_ma_12h = sum(self.demand_history[-12:]) / 12

    if len(self.price_history) >= 12 and len(self.demand_history) >= 12:
      # Classify regime and predict demand
      self.price_regime, self.regime_confidence = self.classify_price_regime(self.price_history)
      demand_spike_prob, _ = self.predict_demand_spike(self.demand_history)

      price_ratio = current_grid_sell_price / (current_grid_buy_price + 0.001)
      recent_momentum = (self.price_history[-1] - self.price_history[-6]) / (self.price_history[-6] + 0.001)

      # Multi-horizon value signal
      net_value = self.compute_multi_horizon_value(
        current_soc, self.price_regime, demand_spike_prob, price_ratio, recent_momentum
      )

      # Decision: magnitude of net_value determines action
      if net_value > 0.4:  # Strong charge signal
        charge_available = min(
          self.charge_rate_kw * (0.8 + 0.2 * min(abs(net_value) / 1.5, 1.0)),
          battery_capacity_kwh * (self.soc_max - 0.05) - current_energy_stored_kwh
        )
        if charge_available > 0.1 and current_soc < self.soc_max - 0.05:
          action_kw = charge_available

      elif net_value < -0.4:  # Strong discharge signal
        discharge_available = min(
          self.discharge_rate_kw * (0.8 + 0.2 * min(abs(net_value) / 1.5, 1.0)),
          current_energy_stored_kwh - battery_capacity_kwh * (self.soc_min + 0.05)
        )
        if discharge_available > 0.1 and current_soc > self.soc_min + 0.05:
          action_kw = -discharge_available

    # Fallback: conservative balancing
    if action_kw == 0.0:
      if current_demand_kw > current_pv_generation_kw and current_soc > 0.20:
        deficit = min(current_demand_kw - current_pv_generation_kw, self.discharge_rate_kw * 0.10)
        action_kw = -min(deficit, current_energy_stored_kwh - battery_capacity_kwh * 0.15)
      elif current_pv_generation_kw > current_demand_kw and current_soc < 0.80:
        excess = min(current_pv_generation_kw - current_demand_kw, self.charge_rate_kw * 0.10)
        action_kw = min(excess, battery_capacity_kwh * 0.80 - current_energy_stored_kwh)

    return action_kw
