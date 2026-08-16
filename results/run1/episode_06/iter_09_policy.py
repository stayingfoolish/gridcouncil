class Policy:
  def __init__(self):
    """Momentum-driven, volatility-aware battery arbitrage."""
    # Exponential moving averages for trend detection
    self.spread_ema_fast = None  # 0.15 alpha, responds in 7 periods
    self.spread_ema_slow = None  # 0.04 alpha, responds in 25 periods
    self.ema_alpha_fast = 0.15
    self.ema_alpha_slow = 0.04

    # Volatility tracking (adaptive risk sizing)
    self.recent_spreads = []
    self.max_history_len = 60  # ~2.5 hours rolling
    self.volatility = 0.004

    # Hourly expectations (learned from patterns)
    self.hour_spread_expectation = [0.0] * 24  # Mean spread per hour
    self.hour_spread_volatility = [0.004] * 24  # Volatility per hour
    self.hour_sample_counts = [0] * 24

    # Adaptive parameters
    self.target_soc = 0.50
    self.battery_capacity = 10.0
    self.min_soc = 0.05
    self.max_soc = 0.98

    # Base thresholds (scaled by volatility)
    self.min_spread_threshold = 0.003  # Abs threshold for action
    self.momentum_threshold = 0.0004   # Spread change direction signal

    self.current_hour = 0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute momentum-following arbitrage with volatility-adaptive sizing."""

    self.battery_capacity = battery_capacity_kwh
    soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0.5
    current_spread = current_grid_sell_price - current_grid_buy_price
    hour_idx = self.current_hour % 24

    # Initialize EMAs on first call
    if self.spread_ema_fast is None:
      self.spread_ema_fast = current_spread
      self.spread_ema_slow = current_spread

    # Update trend indicators
    prev_fast = self.spread_ema_fast
    self.spread_ema_fast = (self.ema_alpha_fast * current_spread +
                            (1 - self.ema_alpha_fast) * self.spread_ema_fast)
    self.spread_ema_slow = (self.ema_alpha_slow * current_spread +
                            (1 - self.ema_alpha_slow) * self.spread_ema_slow)

    # Calculate momentum: Is spread accelerating up or down?
    momentum = self.spread_ema_fast - self.spread_ema_slow
    momentum_direction = 1.0 if momentum > self.momentum_threshold else (-1.0 if momentum < -self.momentum_threshold else 0.0)

    # Update volatility estimate
    self.recent_spreads.append(current_spread)
    if len(self.recent_spreads) > self.max_history_len:
      self.recent_spreads.pop(0)

    if len(self.recent_spreads) > 10:
      mean = sum(self.recent_spreads) / len(self.recent_spreads)
      variance = sum((x - mean) ** 2 for x in self.recent_spreads) / len(self.recent_spreads)
      self.volatility = variance ** 0.5
      self.volatility = max(0.0015, min(0.015, self.volatility))  # Bounded

    # Learn hourly patterns (running mean/std)
    alpha_learn = 0.2
    if self.hour_sample_counts[hour_idx] == 0:
      self.hour_spread_expectation[hour_idx] = current_spread
      self.hour_spread_volatility[hour_idx] = self.volatility
    else:
      self.hour_spread_expectation[hour_idx] = (
        alpha_learn * current_spread +
        (1 - alpha_learn) * self.hour_spread_expectation[hour_idx])
      self.hour_spread_volatility[hour_idx] = (
        alpha_learn * abs(current_spread - self.hour_spread_expectation[hour_idx]) +
        (1 - alpha_learn) * self.hour_spread_volatility[hour_idx])
    self.hour_sample_counts[hour_idx] += 1

    # Spread deviation from hourly expectation (z-score)
    hourly_vol = max(0.002, self.hour_spread_volatility[hour_idx])
    spread_z_score = (current_spread - self.hour_spread_expectation[hour_idx]) / hourly_vol if hourly_vol > 0 else 0.0

    # Volatility-adaptive aggressiveness
    if self.volatility > 0.010:
      # High volatility: conservative, small positions only
      size_multiplier = 0.5
    elif self.volatility < 0.003:
      # Low volatility: can be more aggressive
      size_multiplier = 1.4
    else:
      size_multiplier = 1.0

    # DECISION LOGIC: Momentum + Deviation-Based Sizing
    action_kw = 0.0

    # SELL SIGNAL: Momentum positive (price rising) + above expectation + room to discharge
    if (momentum_direction > 0 and current_spread > self.hour_spread_expectation[hour_idx] and
        current_spread > self.min_spread_threshold and soc > self.min_soc + 0.12):
      # Size by how far above expectation we are
      sell_size = 3.0 + min(5.0, spread_z_score * 2.0)  # 3-8 kW
      action_kw = -1.0 * sell_size * size_multiplier

    # BUY SIGNAL: Momentum negative (price falling) + below expectation + room to charge
    elif (momentum_direction < 0 and current_spread < self.hour_spread_expectation[hour_idx] and
          current_spread < -self.min_spread_threshold and soc < self.max_soc - 0.12):
      # Size by how far below expectation we are
      buy_size = 3.0 + min(5.0, abs(spread_z_score) * 2.0)  # 3-8 kW
      action_kw = buy_size * size_multiplier

    # EXTREME OPPORTUNITY: Rare high-margin events
    elif current_spread > 0.008 and soc > self.min_soc + 0.08:
      action_kw = -7.5 * size_multiplier
    elif current_spread < -0.008 and soc < self.max_soc - 0.08:
      action_kw = 7.5 * size_multiplier

    # LIGHT REBALANCING: Drift back to target SOC when market is neutral
    else:
      soc_error = soc - self.target_soc
      if soc_error > 0.18:
        action_kw = -2.5
      elif soc_error < -0.18:
        action_kw = 2.5
      else:
        action_kw = 0.0

    # Safety constraints
    action_kw = max(-7.0, min(8.0, action_kw))

    self.current_hour += 1

    return action_kw
