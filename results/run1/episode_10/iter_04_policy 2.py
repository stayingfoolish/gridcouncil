class Policy:
  def __init__(self):
    self.price_history = []
    self.demand_history = []
    self.last_action = 0.0
    self.mode_memory = None
    self.max_history_length = 15
    self.volatility_window = 5

  def _calculate_volatility(self):
    """Measure price volatility to detect opportunity windows."""
    if len(self.price_history) < self.volatility_window:
      return 0
    recent_prices = self.price_history[-self.volatility_window:]
    avg = sum(recent_prices) / len(recent_prices)
    variance = sum((p - avg) ** 2 for p in recent_prices) / len(recent_prices)
    return (variance ** 0.5) * 100  # Percentage volatility

  def _calculate_spread_opportunity(self, buy_price, sell_price):
    """Quantify profit potential from buy-sell spread."""
    base_spread = sell_price - buy_price
    # Amplify signal when spread is widest (optimal arbitrage window)
    return max(0, (base_spread - 0.05) * 2.5)

  def _predict_next_price_direction(self):
    """Forecast price movement direction with momentum acceleration."""
    if len(self.price_history) < 4:
      return 0
    # Use acceleration (second derivative) instead of just trend
    recent = self.price_history[-1] - self.price_history[-2]
    prior = self.price_history[-2] - self.price_history[-3]
    acceleration = recent - prior
    if acceleration > 0.002:
      return 1  # Accelerating upward
    elif acceleration < -0.002:
      return -1  # Accelerating downward
    return 0

  def _calculate_demand_hazard(self):
    """Assess immediate demand risk (peak detection)."""
    if len(self.demand_history) < 3:
      return 0
    recent_demand = self.demand_history[-1]
    avg_demand = sum(self.demand_history) / len(self.demand_history)
    peak_ratio = recent_demand / max(avg_demand, 0.1)
    return min(1.0, peak_ratio)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    # Update history
    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Calculate market conditions
    volatility = self._calculate_volatility()
    spread_opportunity = self._calculate_spread_opportunity(current_grid_buy_price, current_grid_sell_price)
    price_momentum = self._predict_next_price_direction()
    demand_hazard = self._calculate_demand_hazard()

    # CRITICAL: Handle immediate demand overrides
    demand_shortfall = max(0.0, current_demand_kw - current_pv_generation_kw)
    if demand_shortfall > 3.0 and current_soc > 0.25:
      return -min(7.0, current_energy_stored_kwh * 0.2)

    action_kw = 0.0

    # MODE 1: HIGH VOLATILITY - AGGRESSIVE ARBITRAGE
    if volatility > 8.0:
      # Exploit peak price spreads
      if spread_opportunity > 0.12 and current_soc < 0.70:
        # Charge during low prices with high volatility
        if current_grid_buy_price < 0.15 or (price_momentum > 0 and volatility > 12.0):
          action_kw = 10.0
        else:
          action_kw = 5.0

      elif spread_opportunity > 0.08 and current_soc > 0.60:
        # Discharge during high prices with high volatility
        if current_grid_sell_price > 0.38 or (price_momentum < 0 and volatility > 12.0):
          action_kw = -6.0
        else:
          action_kw = -3.0

    # MODE 2: LOW VOLATILITY - CONSERVATIVE DEMAND MATCHING
    else:
      # Maintain minimum safety buffer for demand peaks
      min_safe_soc = 0.30 + demand_hazard * 0.25

      if current_soc < min_safe_soc and current_grid_buy_price < 0.20:
        action_kw = 4.0
      elif current_soc > (min_safe_soc + 0.25) and current_grid_sell_price > 0.30:
        action_kw = -2.0

    # Opportunistic PV charging (always consider excess generation)
    excess_pv = max(0.0, current_pv_generation_kw - current_demand_kw)
    if excess_pv > 1.5 and current_soc < 0.80 and action_kw <= 0:
      action_kw = min(3.0, excess_pv * 0.8)

    # Smoothing: softer damping for opportunity capture
    if self.last_action * action_kw > 0:
      action_kw *= 0.85

    # Physical constraints
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh
    if action_kw > 0:
      action_kw = min(action_kw, available_capacity / 0.5)
    else:
      action_kw = max(action_kw, -current_energy_stored_kwh / 0.5)

    self.last_action = action_kw
    return max(-7.0, min(10.0, action_kw))
