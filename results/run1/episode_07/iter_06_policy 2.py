class Policy:
  def __init__(self):
    """Confidence-weighted arbitrage with adaptive thresholds."""
    self.max_charge_rate = 12.0
    self.max_discharge_rate = 10.0

    # Expanded price history for trend detection
    self.price_history = []
    self.price_window = 30  # Extended from 20

    # Trend-based parameters (NEW)
    self.price_momentum_window = 5
    self.volatility_window = 10
    self.trend_confidence_threshold = 0.6

    # Adaptive thresholds (replaced fixed percentiles)
    self.sell_percentile_base = 0.70
    self.charge_percentile_base = 0.30
    self.volatility_scaling = True

    # SOC management (relaxed from [0.2-0.8])
    self.min_soc = 0.15  # More aggressive discharge
    self.max_soc = 0.85  # More aggressive charge
    self.target_soc_neutral = 0.50

    # Confidence decay (NEW)
    self.last_action_time = 0
    self.action_confidence_decay = 0.95

  def calculate_price_metrics(self, current_price):
    """Calculate price percentile, momentum, and volatility."""
    self.price_history.append(current_price)
    if len(self.price_history) > self.price_window:
      self.price_history.pop(0)

    if len(self.price_history) < 5:
      return 0.5, 0.0, 0.0

    # Percentile
    sorted_prices = sorted(self.price_history)
    percentile = len([p for p in sorted_prices if p < current_price]) / len(sorted_prices)

    # Momentum: slope of recent prices
    if len(self.price_history) >= self.price_momentum_window:
      recent = self.price_history[-self.price_momentum_window:]
      momentum = (recent[-1] - recent[0]) / (recent[0] + 1e-6)
    else:
      momentum = 0.0

    # Volatility: std dev of recent returns
    if len(self.price_history) >= self.volatility_window:
      recent = self.price_history[-self.volatility_window:]
      returns = [(recent[i+1] - recent[i]) / (recent[i] + 1e-6) for i in range(len(recent)-1)]
      volatility = (sum(x**2 for x in returns) / len(returns))**0.5
    else:
      volatility = 0.0

    return percentile, momentum, volatility

  def calculate_adaptive_thresholds(self, volatility):
    """Scale thresholds based on volatility: high volatility → more aggressive."""
    if not self.volatility_scaling:
      return self.sell_percentile_base, self.charge_percentile_base

    # High volatility = widen gap between thresholds, push toward extremes
    volatility_factor = 1.0 + (volatility * 3.0)  # Amplify volatility signal
    sell_threshold = min(0.85, self.sell_percentile_base + (volatility * 0.15))
    charge_threshold = max(0.15, self.charge_percentile_base - (volatility * 0.15))

    return sell_threshold, charge_threshold

  def calculate_confidence_scores(self, price_percentile, momentum, volatility, surplus_pv, deficit_demand, current_soc):
    """Generate confidence scores for each action."""
    charge_confidence = 0.0
    discharge_confidence = 0.0

    sell_threshold, charge_threshold = self.calculate_adaptive_thresholds(volatility)

    # DISCHARGE confidence
    if price_percentile > sell_threshold and current_soc > self.min_soc:
      discharge_confidence = min(1.0, (price_percentile - sell_threshold) / (1 - sell_threshold))
      if momentum > 0:  # Price rising: boost discharge (sell before peak)
        discharge_confidence = min(1.0, discharge_confidence * (1 + momentum * 2))

    # CHARGE confidence
    if price_percentile < charge_threshold and current_soc < self.max_soc:
      charge_confidence = min(1.0, (charge_threshold - price_percentile) / charge_threshold)
      if momentum < 0:  # Price falling: boost charge (buy before trough)
        charge_confidence = min(1.0, charge_confidence * (1 - momentum * 2))

    # PV surplus: boost charge (free energy)
    if surplus_pv > 1.0 and current_soc < self.max_soc:
      charge_confidence = min(1.0, max(charge_confidence, surplus_pv / 5.0))

    # Demand deficit: boost discharge
    if deficit_demand > 1.0 and current_soc > self.min_soc:
      discharge_confidence = min(1.0, max(discharge_confidence, deficit_demand / 5.0))

    return charge_confidence, discharge_confidence

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Confidence-weighted continuous action."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Calculate metrics
    price_percentile, momentum, volatility = self.calculate_price_metrics(current_grid_buy_price)

    surplus_pv = max(0, current_pv_generation_kw - current_demand_kw)
    deficit_demand = max(0, current_demand_kw - current_pv_generation_kw)

    # Compute confidence scores
    charge_conf, discharge_conf = self.calculate_confidence_scores(
      price_percentile, momentum, volatility, surplus_pv, deficit_demand, current_soc
    )

    # Energy constraints
    min_energy = battery_capacity_kwh * self.min_soc
    max_energy = battery_capacity_kwh * self.max_soc
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)
    available_to_charge = max(0, max_energy - current_energy_stored_kwh)

    # Net action: higher discharge confidence → negative, higher charge → positive
    net_confidence = charge_conf - discharge_conf

    if net_confidence > 0.1 and available_to_charge > 0:
      # CHARGE with confidence scaling
      rate = self.max_charge_rate * net_confidence
      return min(rate, available_to_charge)

    elif net_confidence < -0.1 and available_to_discharge > 0:
      # DISCHARGE with confidence scaling
      rate = self.max_discharge_rate * abs(net_confidence)
      return -min(rate, available_to_discharge)

    else:
      # NEUTRAL zone: gentle balancing toward target
      soc_error = current_soc - self.target_soc_neutral
      if abs(soc_error) > 0.05:
        rate = self.max_charge_rate * 0.3 * (-soc_error / 0.3)  # Smooth correction
        return max(-available_to_discharge, min(available_to_charge, rate))
      return 0.0
