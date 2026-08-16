class Policy:
  def __init__(self):
    """Predictive arbitrage with reinforcement-based adaptation."""
    self.price_history = []
    self.demand_history = []
    self.reward_history = []
    self.max_history = 36

    self.charge_rate_kw = 2.0
    self.discharge_rate_kw = 2.0

    # State constraints
    self.soc_min = 0.05
    self.soc_max = 0.95
    self.soc_safety_min = 0.10
    self.soc_safety_max = 0.90

    # Predictive parameters
    self.lookahead_window = 12
    self.min_spread_threshold = 1.10  # Only trade if spread > 10%

    # Adaptive parameters (learned from outcomes)
    self.aggressiveness = 0.5  # Controls SOC operating range
    self.recent_success_rate = 0.5

  def predict_price_trend(self, price_history, periods_ahead=6):
    """Estimate price momentum and volatility over next horizon."""
    if len(price_history) < 4:
      return 0.0, 0.08

    # Calculate recent momentum
    momentum = (price_history[-1] - price_history[-4]) / (price_history[-4] + 0.001)

    # Estimate volatility
    recent = price_history[-6:]
    mean = sum(recent) / len(recent)
    volatility = (sum((p - mean)**2 for p in recent) / len(recent)) ** 0.5

    return momentum, volatility

  def compute_arbitrage_value(self, action_type, current_soc, price_momentum, spread_ratio):
    """Calculate expected profit from action over lookahead horizon."""
    if action_type == "discharge":
      # Discharge value = spread premium + price appreciation potential
      value = max(0, (spread_ratio - 1.0)) * 2.0  # Spread is primary signal
      value += max(0, price_momentum) * 0.5  # Bonus if prices rising
      value -= max(0, 1.0 - current_soc) * 0.3  # Penalty for low SOC
      return min(value, 2.0)

    elif action_type == "charge":
      # Charge value = expected spread widening + price decline potential
      value = max(0, (spread_ratio - 1.0)) * 1.5  # Spread signals opportunity cost
      value += max(0, -price_momentum) * 0.4  # Bonus if prices falling
      value -= max(0, current_soc - 0.5) * 0.3  # Penalty for high SOC
      return min(value, 2.0)

    return 0.0

  def adapt_from_performance(self, reward):
    """Update aggressiveness based on recent actions."""
    self.reward_history.append(reward)
    if len(self.reward_history) > 20:
      self.reward_history.pop(0)

    # Success rate = fraction of negative-cost outcomes
    if len(self.reward_history) >= 10:
      recent = self.reward_history[-10:]
      success = sum(1 for r in recent if r < 0) / len(recent)
      self.recent_success_rate = 0.7 * self.recent_success_rate + 0.3 * success

      # If succeeding: expand SOC range for more aggressive arbitrage
      # If failing: contract to conservative bounds
      target = 0.4 + (self.recent_success_rate * 0.4)
      self.aggressiveness = 0.85 * self.aggressiveness + 0.15 * target

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute value-based arbitrage strategy."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    action_kw = 0.0

    if len(self.price_history) >= 4:
      self.adapt_from_performance(current_grid_buy_price - current_grid_sell_price)

      spread_ratio = current_grid_sell_price / (current_grid_buy_price + 0.001)
      price_momentum, volatility = self.predict_price_trend(self.price_history)

      # Dynamic SOC bounds: aggressive when succeeding
      soc_range = (self.soc_max - self.soc_min) * self.aggressiveness
      soc_floor = self.soc_min + (self.soc_safety_min - self.soc_min) * (1 - self.aggressiveness)
      soc_ceiling = self.soc_max - (self.soc_max - self.soc_safety_max) * (1 - self.aggressiveness)

      # Compute value signals
      discharge_val = self.compute_arbitrage_value("discharge", current_soc, price_momentum, spread_ratio)
      charge_val = self.compute_arbitrage_value("charge", current_soc, price_momentum, spread_ratio)

      # Execute if value signal is strong and spread justifies trade
      if discharge_val > charge_val and spread_ratio > self.min_spread_threshold and current_soc > soc_floor:
        discharge_available = min(
          self.discharge_rate_kw * (0.65 + 0.35 * min(discharge_val / 2.0, 1.0)),
          current_energy_stored_kwh - battery_capacity_kwh * soc_floor
        )
        if discharge_available > 0.15:
          action_kw = -discharge_available

      elif charge_val > discharge_val and spread_ratio < 1.05 and current_soc < soc_ceiling:
        charge_available = min(
          self.charge_rate_kw * (0.65 + 0.35 * min(charge_val / 2.0, 1.0)),
          battery_capacity_kwh * soc_ceiling - current_energy_stored_kwh
        )
        if charge_available > 0.15:
          action_kw = charge_available

    # Fallback: light balancing only
    if action_kw == 0.0:
      if current_demand_kw > current_pv_generation_kw and current_soc > self.soc_safety_min + 0.08:
        deficit = min(current_demand_kw - current_pv_generation_kw, self.discharge_rate_kw * 0.15)
        action_kw = -deficit
      elif current_pv_generation_kw > current_demand_kw and current_soc < self.soc_safety_max - 0.08:
        excess = min(current_pv_generation_kw - current_demand_kw, self.charge_rate_kw * 0.15)
        action_kw = excess

    return action_kw
