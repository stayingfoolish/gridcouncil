class Policy:
  def __init__(self):
    """Aggressive margin-capture policy with demand-aware arbitrage."""
    # Hourly pattern tracking (24-hour cycle)
    self.hourly_spreads = [[] for _ in range(24)]
    self.hourly_volatility = [0.0] * 24
    self.hourly_pv = [[] for _ in range(24)]
    self.hourly_demand = [[] for _ in range(24)]
    self.current_hour = 0

    # Price statistics for dynamic thresholding
    self.spread_history = []
    self.max_history_length = 168  # Full week

    # Trading state machine
    self.is_charging_position = False
    self.position_entry_spread = 0.0
    self.position_entry_soc = 0.5

    # Parameters
    self.battery_capacity = 10.0  # Will be updated
    self.min_soc = 0.05
    self.max_soc = 0.98
    self.target_soc = 0.50

    # Adaptive parameters
    self.aggressiveness = 1.0  # Scales all actions
    self.min_spread_percentile = 20  # Target spreads in bottom 20% (most negative)
    self.max_spread_percentile = 80  # Target spreads in top 80% (most positive)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute aggressive arbitrage with demand-aware positioning."""

    self.battery_capacity = battery_capacity_kwh
    soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0.5
    current_spread = current_grid_sell_price - current_grid_buy_price
    hour_idx = self.current_hour % 24

    # Track prices and demand
    self.spread_history.append(current_spread)
    if len(self.spread_history) > self.max_history_length:
      self.spread_history.pop(0)

    self.hourly_spreads[hour_idx].append(current_spread)
    if len(self.hourly_spreads[hour_idx]) > 100:
      self.hourly_spreads[hour_idx].pop(0)

    self.hourly_pv[hour_idx].append(current_pv_generation_kw)
    if len(self.hourly_pv[hour_idx]) > 100:
      self.hourly_pv[hour_idx].pop(0)

    self.hourly_demand[hour_idx].append(current_demand_kw)
    if len(self.hourly_demand[hour_idx]) > 100:
      self.hourly_demand[hour_idx].pop(0)

    # Calculate dynamic percentile thresholds
    if len(self.spread_history) > 10:
      spread_sorted = sorted(self.spread_history)
      min_threshold = spread_sorted[int(len(spread_sorted) * self.min_spread_percentile / 100)]
      max_threshold = spread_sorted[int(len(spread_sorted) * self.max_spread_percentile / 100)]
    else:
      min_threshold = -0.05
      max_threshold = 0.02

    # Calculate next 24-hour opportunity score
    opportunity_score = 0.0
    lookahead_spreads = []
    lookahead_action = 0.0

    for i in range(1, 25):
      next_hour_idx = (hour_idx + i) % 24
      if len(self.hourly_spreads[next_hour_idx]) > 0:
        avg_spread = sum(self.hourly_spreads[next_hour_idx]) / len(self.hourly_spreads[next_hour_idx])
        lookahead_spreads.append(avg_spread)
        if avg_spread > max_threshold and avg_spread > 0.005:
          opportunity_score += 1.0
        elif avg_spread < min_threshold and avg_spread < -0.005:
          opportunity_score -= 1.0

    # STATE MACHINE: Hierarchical decision making
    action_kw = 0.0

    # STATE 1: EXPLOIT EXCEPTIONAL SELL OPPORTUNITY (highest priority)
    if current_spread > max_threshold and current_spread > 0.008 and soc > self.min_soc + 0.1:
      action_kw = -8.0 * self.aggressiveness
      self.is_charging_position = False

    # STATE 2: EXPLOIT EXCEPTIONAL BUY OPPORTUNITY
    elif current_spread < min_threshold and current_spread < -0.008 and soc < self.max_soc - 0.1:
      action_kw = 8.0 * self.aggressiveness
      self.is_charging_position = True
      self.position_entry_spread = current_spread

    # STATE 3: POSITIONED FOR IMMINENT OPPORTUNITY
    elif (opportunity_score > 2.0 and not self.is_charging_position and
          soc < self.max_soc - 0.15):
      action_kw = 6.0 * self.aggressiveness
      self.is_charging_position = True
      self.position_entry_spread = current_spread

    elif (opportunity_score < -2.0 and self.is_charging_position and
          soc > self.min_soc + 0.15):
      action_kw = -6.0 * self.aggressiveness
      self.is_charging_position = False

    # STATE 4: CAPITALIZE ON VOLATILITY
    elif abs(current_spread) > 0.006:
      if current_spread > 0.003 and soc > self.min_soc + 0.2:
        action_kw = -4.0 * self.aggressiveness
      elif current_spread < -0.003 and soc < self.max_soc - 0.2:
        action_kw = 4.0 * self.aggressiveness

    # STATE 5: GENTLE REBALANCING TO TARGET
    else:
      soc_error = soc - self.target_soc
      if soc_error > 0.12:
        action_kw = -3.0 * self.aggressiveness
      elif soc_error < -0.12:
        action_kw = 3.0 * self.aggressiveness
      else:
        action_kw = 0.0

    # Apply constraints
    action_kw = max(-5.0, min(10.0, action_kw))

    # Advance time
    self.current_hour += 1

    return action_kw
