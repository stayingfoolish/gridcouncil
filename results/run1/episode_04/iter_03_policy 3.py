import math

class Policy:
  def __init__(self):
    """Adaptive market-regime battery policy with dynamic thresholds and sizing."""
    self.price_history = []
    self.max_history_length = 48  # Extended to 2 days of hourly data

    # Adaptive parameters (will be overridden by regime)
    self.charge_target_soc = 0.85
    self.discharge_target_soc = 0.25
    self.momentum_threshold = 0.5
    self.spread_threshold = 4.0
    self.max_charge_kw = 12.0
    self.max_discharge_kw = 8.0

    # Regime state tracking
    self.recent_actions = []
    self.max_actions_history = 10
    self.volatility_ma = 0.0
    self.action_success_rate = 0.5

  def _calculate_regime_metrics(self, prices):
    """Analyze price dynamics to determine market regime."""
    if len(prices) < 8:
      return 'initialization', 0.0, 0.0, 0.0

    # Calculate volatility (standard deviation of 8-period returns)
    recent_8 = prices[-8:]
    returns = [recent_8[i+1] - recent_8[i] for i in range(len(recent_8)-1)]
    volatility = math.sqrt(sum(r**2 for r in returns) / len(returns)) if returns else 0.0

    # Calculate trend strength (price range as % of average)
    recent_range = max(recent_8) - min(recent_8)
    avg_price = sum(recent_8) / len(recent_8)
    trend_strength = (recent_range / avg_price * 100) if avg_price > 0 else 0.0

    # Calculate acceleration (second derivative of momentum)
    if len(prices) >= 16:
      m1 = (sum(prices[-4:]) / 4) - (sum(prices[-8:-4]) / 4)
      m2 = (sum(prices[-12:-8]) / 4) - (sum(prices[-16:-12]) / 4)
      acceleration = m1 - m2
    else:
      acceleration = 0.0

    # Regime classification
    if volatility > 2.5:
      regime = 'high_volatility'
    elif trend_strength > 15.0:
      regime = 'trending'
    elif abs(acceleration) > 1.0:
      regime = 'accelerating'
    else:
      regime = 'stable'

    return regime, volatility, trend_strength, acceleration

  def _adapt_parameters(self, regime, volatility, trend_strength, battery_level_ratio, price_spread):
    """Dynamically adjust action parameters based on market regime."""

    if regime == 'high_volatility':
      # In volatile markets: widen thresholds to avoid whipsaws, but act larger when confident
      self.momentum_threshold = 0.3  # Lower threshold to catch swings
      self.spread_threshold = 2.0    # Lower spread threshold
      self.max_charge_kw = 10.0      # Conservative sizing
      self.max_discharge_kw = 7.0

      # Reduce battery targets to preserve optionality
      self.charge_target_soc = 0.75
      self.discharge_target_soc = 0.35

    elif regime == 'trending':
      # In trending markets: aggressive position-building
      self.momentum_threshold = 0.7  # Higher threshold for strong trends only
      self.spread_threshold = 5.5
      self.max_charge_kw = min(14.0, 10.0 + (trend_strength / 15.0) * 4)  # Scale with trend
      self.max_discharge_kw = min(9.5, 8.0 + (trend_strength / 15.0) * 1.5)

      self.charge_target_soc = 0.90
      self.discharge_target_soc = 0.20

    elif regime == 'accelerating':
      # Accelerating market: chase the momentum
      self.momentum_threshold = 0.25
      self.spread_threshold = 3.5
      self.max_charge_kw = 13.0
      self.max_discharge_kw = 8.5

      self.charge_target_soc = 0.82
      self.discharge_target_soc = 0.28

    else:  # stable
      # Stable market: conservative, opportunistic
      self.momentum_threshold = 0.6
      self.spread_threshold = 4.5
      self.max_charge_kw = 10.0
      self.max_discharge_kw = 7.5

      self.charge_target_soc = 0.85
      self.discharge_target_soc = 0.25

  def _size_action(self, action_direction, battery_level_ratio, available_capacity_kwh,
                   confidence, regime):
    """Size action magnitude based on confidence and market conditions."""

    base_size = self.max_charge_kw if action_direction > 0 else self.max_discharge_kw

    # Scale by confidence (0.0 to 1.0)
    sized_action = base_size * confidence

    # Apply battery constraints
    if action_direction > 0:
      sized_action = min(sized_action, available_capacity_kwh)
    else:
      sized_action = min(sized_action, battery_level_ratio * available_capacity_kwh * 0.9)

    # In trending markets, be more aggressive; in volatile, be more cautious
    if regime == 'trending' and confidence > 0.6:
      sized_action *= 1.15
    elif regime == 'high_volatility' and confidence < 0.5:
      sized_action *= 0.7

    return sized_action if action_direction > 0 else -sized_action

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Regime-adaptive battery arbitrage with dynamic parameter tuning."""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)

    # Calculate regime and market metrics
    regime, volatility, trend_strength, acceleration = self._calculate_regime_metrics(self.price_history)

    # Adapt parameters to regime
    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    price_spread = current_grid_sell_price - current_grid_buy_price
    self._adapt_parameters(regime, volatility, trend_strength, battery_level_ratio, price_spread)

    # Multi-timeframe momentum analysis
    if len(self.price_history) >= 16:
      momentum_short = (sum(self.price_history[-4:]) / 4) - (sum(self.price_history[-8:-4]) / 4)
      momentum_medium = (sum(self.price_history[-8:-4]) / 4) - (sum(self.price_history[-16:-8]) / 8)
      momentum_aligned = momentum_short * momentum_medium > 0  # Both same direction
      momentum_strength = abs(momentum_short)
    else:
      momentum_short = 0
      momentum_aligned = False
      momentum_strength = 0

    net_supply = current_pv_generation_kw - current_demand_kw
    action_kw = 0.0
    action_confidence = 0.0

    # Strategy 1: Aligned multi-timeframe momentum (highest confidence)
    if momentum_aligned and momentum_strength > self.momentum_threshold:
      if momentum_short < -self.momentum_threshold and battery_level_ratio < self.charge_target_soc:
        action_direction = 1
        action_confidence = min(0.95, 0.5 + momentum_strength / 2.0)
      elif momentum_short > self.momentum_threshold and battery_level_ratio > self.discharge_target_soc:
        action_direction = -1
        action_confidence = min(0.95, 0.5 + momentum_strength / 2.0)
      else:
        action_direction = 0

    # Strategy 2: Price spread arbitrage (medium confidence)
    elif len(self.price_history) >= 2:
      spread_percentage = (price_spread / current_grid_buy_price * 100) if current_grid_buy_price > 0 else 0

      if spread_percentage < -self.spread_threshold and battery_level_ratio < 0.8:
        action_direction = 1
        action_confidence = min(0.8, 0.4 + abs(spread_percentage) / 10.0)
      elif spread_percentage > self.spread_threshold and battery_level_ratio > 0.3:
        action_direction = -1
        action_confidence = min(0.8, 0.4 + abs(spread_percentage) / 10.0)
      else:
        action_direction = 0

    # Strategy 3: Supply-demand balancing (lower confidence, fallback)
    else:
      if net_supply > 2.0 and battery_level_ratio < 0.95:
        action_direction = 1
        action_confidence = 0.3 + (net_supply / 10.0)
      elif net_supply < -2.0 and battery_level_ratio > 0.15:
        action_direction = -1
        action_confidence = 0.3 + abs(net_supply) / 10.0
      else:
        action_direction = 0

    # Size action based on confidence and regime
    if action_direction != 0:
      available_cap = battery_capacity_kwh - current_energy_stored_kwh if action_direction > 0 else current_energy_stored_kwh
      action_kw = self._size_action(action_direction, battery_level_ratio, available_cap,
                                    action_confidence, regime)

    return action_kw
