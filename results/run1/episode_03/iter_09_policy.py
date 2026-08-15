class Policy:
  def __init__(self):
    """Confidence-weighted opportunity detection for arbitrage"""
    self.price_history = []
    self.pv_history = []
    self.demand_history = []
    self.trade_results = []  # Track (action, profit) pairs

    self.max_history = 72  # Extend to 3-day window for pattern detection
    self.recent_window = 6  # Last 6 hours for immediate trends

    # Confidence thresholds - only trade when multiple signals align
    self.momentum_threshold = 0.08  # 8% price change needed
    self.volatility_threshold = 8.0  # Minimum volatility to trade
    self.pattern_confidence_required = 0.65  # 65% confidence needed

    # Adaptive profit margins based on recent performance
    self.base_profit_margin = 0.04  # 4% base
    self.margin_adjustment = 0.0  # Adjusted based on trade accuracy
    self.win_rate = 0.5

    # Target SOC ranges (wider than current)
    self.charge_target_min = 0.30
    self.charge_target_max = 0.75
    self.discharge_safety_floor = 0.15

  def _detect_price_direction(self):
    """Multi-signal price direction detection with confidence scoring"""
    if len(self.price_history) < 12:
      return {'direction': 'neutral', 'confidence': 0.0, 'signal_count': 0}

    signals = 0
    confidence = 0.0

    # Signal 1: Short-term momentum (last 6 hours vs previous 6 hours)
    recent_6 = self.price_history[-6:]
    prior_6 = self.price_history[-12:-6]
    momentum = (sum(recent_6) - sum(prior_6)) / (sum(prior_6) + 0.001)

    if abs(momentum) > 0.08:
      signals += 1
      confidence += min(abs(momentum) / 0.2, 1.0)  # Scale up to 1.0 at 20% change

    # Signal 2: Volatility spike detection (current vol > historical avg)
    all_prices = self.price_history[-24:]
    current_vol = max(recent_6) - min(recent_6)
    historical_vol = max(all_prices) - min(all_prices)
    vol_ratio = current_vol / (historical_vol + 0.001)

    if vol_ratio > 1.2:  # 20% higher volatility than recent average
      signals += 1
      confidence += min((vol_ratio - 1.0) / 0.5, 1.0)  # Scale to 1.0 at 50% above avg

    # Signal 3: Multi-timeframe alignment (6h momentum same direction as 24h)
    momentum_24 = (sum(recent_6) - sum(self.price_history[-24:-18])) / (sum(self.price_history[-24:-18]) + 0.001)

    if (momentum > 0 and momentum_24 > 0) or (momentum < 0 and momentum_24 < 0):
      signals += 1
      confidence += 0.3  # Alignment gives bonus confidence

    # Determine direction from momentum
    direction = 'up' if momentum > 0 else ('down' if momentum < 0 else 'neutral')

    # Normalize confidence
    final_confidence = min(confidence / 3.0, 1.0)

    return {
      'direction': direction,
      'confidence': final_confidence,
      'signal_count': signals,
      'momentum': momentum,
      'volatility_ratio': vol_ratio
    }

  def _update_adaptive_margins(self):
    """Adjust profit margin based on recent trade accuracy"""
    if len(self.trade_results) >= 10:
      recent_trades = self.trade_results[-10:]
      wins = sum(1 for _, profit in recent_trades if profit > 0)
      self.win_rate = wins / len(recent_trades)

      # If win rate is low, increase margin requirement
      if self.win_rate < 0.4:
        self.margin_adjustment = 0.03  # Add 3% margin requirement
      elif self.win_rate > 0.65:
        self.margin_adjustment = -0.02  # Reduce margin requirement
      else:
        self.margin_adjustment = 0.0  # Stay neutral

  def _estimate_next_extreme(self):
    """Estimate likely price high and low for next 12 hours"""
    if len(self.price_history) < 24:
      return {'likely_high': 50, 'likely_low': 50}

    recent_prices = self.price_history[-12:]
    recent_range = max(recent_prices) - min(recent_prices)
    recent_avg = sum(recent_prices) / len(recent_prices)

    # Use volatility to project range
    likely_high = recent_avg + recent_range * 1.1
    likely_low = recent_avg - recent_range * 0.9

    return {'likely_high': likely_high, 'likely_low': likely_low}

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Confidence-weighted opportunity detection"""

    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    self.price_history.append(current_grid_buy_price)
    self.pv_history.append(current_pv_generation_kw)
    self.demand_history.append(current_demand_kw)

    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
    if len(self.pv_history) > self.max_history:
      self.pv_history.pop(0)
    if len(self.demand_history) > self.max_history:
      self.demand_history.pop(0)

    self._update_adaptive_margins()

    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    pv_excess = current_pv_generation_kw - current_demand_kw
    demand_deficit = current_demand_kw - current_pv_generation_kw

    direction = self._detect_price_direction()
    extremes = self._estimate_next_extreme()

    action_kw = 0.0

    # Priority 1: Critical demand - always support if possible
    if demand_deficit > 3.0 and soc_ratio > self.discharge_safety_floor + 0.10:
      action_kw = -min(demand_deficit * 0.8, max_discharge_rate)
      return action_kw

    # Priority 2: PV harvesting - renewable always better than grid
    if pv_excess > 2.5 and soc_ratio < 0.92:
      action_kw = min(pv_excess * 0.9, max_charge_rate)
      return action_kw

    # Priority 3: High-confidence arbitrage only
    required_margin = self.base_profit_margin + self.margin_adjustment

    # Only trade when confidence AND margin AND SOC allows
    if direction['confidence'] >= self.pattern_confidence_required:

      if direction['direction'] == 'up' and soc_ratio < self.charge_target_max:
        # Price expected to rise - charge now while cheap
        spread_potential = (extremes['likely_high'] - current_grid_buy_price) / current_grid_buy_price

        if spread_potential > required_margin:
          charge_amount = min(
            max_charge_rate * (0.6 + direction['confidence'] * 0.4),
            (self.charge_target_max - soc_ratio) * battery_capacity_kwh / 3
          )
          action_kw = charge_amount
          return action_kw

      elif direction['direction'] == 'down' and soc_ratio > self.discharge_safety_floor + 0.20:
        # Price expected to fall - sell now while expensive
        spread_potential = (current_grid_sell_price - extremes['likely_low']) / current_grid_sell_price

        if spread_potential > required_margin:
          discharge_amount = min(
            max_discharge_rate * (0.6 + direction['confidence'] * 0.4),
            (soc_ratio - self.discharge_safety_floor) * battery_capacity_kwh / 3
          )
          action_kw = -discharge_amount
          return action_kw

    # Priority 4: Gentle rebalancing toward midpoint if confident
    if soc_ratio < self.charge_target_min and direction['confidence'] < 0.4:
      action_kw = max_charge_rate * 0.2
    elif soc_ratio > self.charge_target_max and direction['confidence'] < 0.4:
      action_kw = -max_discharge_rate * 0.2

    return action_kw
