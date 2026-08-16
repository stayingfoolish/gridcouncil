class Policy:
  def __init__(self):
    """Initializes the policy with time-aware predictive arbitrage capabilities."""
    # Hourly pattern tracking
    self.hourly_spreads = [[] for _ in range(24)]
    self.hourly_confidence = [0.0] * 24
    self.current_hour = 0

    # Historical tracking for adaptive thresholds
    self.recent_trades = []

    # Parameters
    self.soc_target = 0.45
    self.soc_deadband = 0.07
    self.battery_degradation_cost = 0.0008
    self.base_arbitrage_threshold = 0.03
    self.min_profitable_spread = 0.025

    # Volatility tracking
    self.recent_prices = []
    self.volatility_window = 6

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on time-aware predictive arbitrage."""

    # Calculate current state
    soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0.5
    soc_deviation = abs(soc - self.soc_target)

    # Calculate current spread
    current_spread = current_grid_sell_price - current_grid_buy_price

    # Track price history for volatility
    self.recent_prices.append(current_grid_buy_price)
    if len(self.recent_prices) > self.volatility_window:
      self.recent_prices.pop(0)

    # Calculate volatility
    if len(self.recent_prices) > 1:
      price_range = max(self.recent_prices) - min(self.recent_prices)
      avg_price = sum(self.recent_prices) / len(self.recent_prices)
      price_std = price_range / (avg_price + 0.001)
      is_high_volatility = price_std > 0.15
    else:
      is_high_volatility = False

    # Update hourly statistics
    hour_idx = self.current_hour % 24
    self.hourly_spreads[hour_idx].append(current_spread)
    if len(self.hourly_spreads[hour_idx]) > 100:
      self.hourly_spreads[hour_idx].pop(0)

    # Update confidence scores
    for i in range(24):
      if len(self.hourly_spreads[i]) > 0:
        self.hourly_confidence[i] = min(0.9, 0.1 + len(self.hourly_spreads[i]) / 100.0)
      else:
        self.hourly_confidence[i] = 0.0

    # Calculate adaptive threshold based on recent performance
    recent_cost_penalty = 0.0
    if len(self.recent_trades) >= 3:
      profitable_count = sum(1 for pnl in self.recent_trades[-3:] if pnl < 0)
      if profitable_count >= 2:
        recent_cost_penalty = -0.2
      elif profitable_count == 0:
        recent_cost_penalty = 0.3

    adaptive_threshold = self.base_arbitrage_threshold * (1.0 + recent_cost_penalty)

    # Predict next 4-hour spread trajectory
    next_4h_spreads = []
    for i in range(1, 5):
      next_hour_idx = (hour_idx + i) % 24
      if len(self.hourly_spreads[next_hour_idx]) > 0:
        avg_spread = sum(self.hourly_spreads[next_hour_idx]) / len(self.hourly_spreads[next_hour_idx])
        next_4h_spreads.append(avg_spread)

    next_4h_avg_spread = sum(next_4h_spreads) / len(next_4h_spreads) if next_4h_spreads else current_spread
    next_4h_confidence = sum(self.hourly_confidence[(hour_idx + i) % 24] for i in range(1, 5)) / 4.0

    # Safety boundaries
    min_soc = 0.05
    max_soc = 0.98

    action_kw = 0.0

    # PHASE 1: PRE-POSITIONING (look ahead 4 hours)
    if next_4h_avg_spread > adaptive_threshold and next_4h_confidence > 0.4:
      if next_4h_avg_spread > 0:
        if soc < 0.90:
          action_kw = 8.0
        else:
          action_kw = 0.0
      else:
        if soc > 0.10:
          action_kw = -5.0
        else:
          action_kw = 0.0

    # PHASE 2: IMMEDIATE ARBITRAGE
    elif current_spread > adaptive_threshold and soc_deviation < self.soc_deadband:
      if current_spread > 0:
        if soc > min_soc + 0.15:
          action_kw = 8.0
        else:
          action_kw = 2.0
      else:
        if soc < max_soc - 0.15:
          action_kw = -5.0
        else:
          action_kw = -2.0

    # PHASE 3: VOLATILITY CAPTURE
    elif is_high_volatility and current_spread > adaptive_threshold * 0.8:
      if soc_deviation < 0.05:
        if current_spread > 0:
          action_kw = 3.0
        else:
          action_kw = -3.0
      else:
        action_kw = 0.0

    # PHASE 4: GENTLE REBALANCING
    else:
      soc_error = soc - self.soc_target
      if abs(soc_error) > self.soc_deadband:
        if soc_error > 0:
          action_kw = -1.5
        else:
          action_kw = 1.5
      else:
        action_kw = 0.0

    # Apply power constraints
    action_kw = max(-5.0, min(10.0, action_kw))

    # Track trade for learning
    if action_kw != 0:
      pnl = current_spread * abs(action_kw) * 0.25
      self.recent_trades.append(pnl)
      if len(self.recent_trades) > 20:
        self.recent_trades.pop(0)

    # Advance time
    self.current_hour += 1

    return action_kw
