class Policy:
  def __init__(self):
    """Volatility-driven dynamic arbitrage with momentum trading."""
    self.max_charge_rate = 12.0
    self.max_discharge_rate = 10.0

    # Price tracking for volatility calculation
    self.recent_prices = []
    self.recent_window = 20  # Slightly longer for volatility signal
    self.price_history_extended = []  # For trend detection
    self.extended_window = 60

    # Volatility-based thresholds
    self.base_spread_threshold = 0.02  # Much lower base: 2% instead of 5-8%
    self.volatility_multiplier = 1.5   # Reduce thresholds when volatility is HIGH
    self.min_volatility_threshold = 0.005  # Absolute floor on threshold

    # Momentum tracking
    self.last_buy_price = None
    self.price_momentum = 0  # -1 (down), 0 (neutral), 1 (up)
    self.momentum_window = 3

    # Aggressive SOC targets
    self.charge_target = 0.88
    self.discharge_target = 0.12

    # Fast state machine (minimal persistence)
    self.state = "NEUTRAL"
    self.state_age = 0
    self.min_state_duration = 1  # REDUCED: allow faster switching

    # Spread capture targets
    self.min_spread_for_action = 0.015  # Absolute minimum

  def calculate_volatility(self, prices):
    """Calculate rolling volatility (coefficient of variation)."""
    if len(prices) < 2:
      return 0.01  # Safe default
    mean_price = sum(prices) / len(prices)
    if mean_price < 1e-6:
      return 0.01
    variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
    volatility = (variance ** 0.5) / mean_price  # Normalized volatility
    return volatility

  def update_price_history(self, price):
    """Track both recent and extended price history."""
    self.recent_prices.append(price)
    self.price_history_extended.append(price)
    if len(self.recent_prices) > self.recent_window:
      self.recent_prices.pop(0)
    if len(self.price_history_extended) > self.extended_window:
      self.price_history_extended.pop(0)

  def calculate_momentum(self):
    """Detect short-term price trend."""
    if len(self.price_history_extended) < self.momentum_window + 1:
      return 0
    recent_segment = self.price_history_extended[-self.momentum_window:]
    ups = sum(1 for i in range(len(recent_segment) - 1) if recent_segment[i+1] > recent_segment[i])
    downs = sum(1 for i in range(len(recent_segment) - 1) if recent_segment[i+1] < recent_segment[i])
    if ups > downs:
      return 1  # Uptrend
    elif downs > ups:
      return -1  # Downtrend
    return 0  # Neutral

  def get_dynamic_thresholds(self, volatility):
    """Set spread thresholds based on volatility."""
    if volatility < 0.01:
      # Low volatility: require higher spreads to justify action
      return self.base_spread_threshold * 1.5
    elif volatility > 0.05:
      # High volatility: trade on smaller spreads
      return max(self.min_volatility_threshold, self.base_spread_threshold / self.volatility_multiplier)
    else:
      # Medium volatility: interpolate
      factor = 1 + (0.05 - volatility) / 0.04 * 0.5  # Smooth interpolation
      return self.base_spread_threshold * factor

  def evaluate_charge_action(self, current_soc, spread, volatility, momentum, available_to_charge):
    """Charge when spread AND volatility signal opportunity."""
    if available_to_charge < 0.1:
      return 0.0

    threshold = self.get_dynamic_thresholds(volatility)
    if spread < threshold:
      return 0.0

    # Boost if price trending DOWN (good entry)
    momentum_bonus = 1.0
    if momentum < 0:
      momentum_bonus = 1.3

    energy_gap = max(0, (self.charge_target - current_soc) * 100)
    rate = self.max_charge_rate * (0.6 + energy_gap / 150) * momentum_bonus
    return min(rate, available_to_charge)

  def evaluate_discharge_action(self, current_soc, spread, volatility, momentum, available_to_discharge):
    """Discharge when spread AND volatility signal opportunity."""
    if available_to_discharge < 0.1:
      return 0.0

    threshold = self.get_dynamic_thresholds(volatility)
    if spread < threshold:
      return 0.0

    # Boost if price trending UP (good exit)
    momentum_bonus = 1.0
    if momentum > 0:
      momentum_bonus = 1.3

    energy_gap = max(0, (current_soc - self.discharge_target) * 100)
    rate = self.max_discharge_rate * (0.6 + energy_gap / 150) * momentum_bonus
    return min(rate, available_to_discharge)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Volatility-driven dynamic arbitrage with momentum signals."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Update price tracking
    self.update_price_history(current_grid_buy_price)

    # Calculate signals
    volatility = self.calculate_volatility(self.recent_prices)
    momentum = self.calculate_momentum()
    spread = max(0.0, (current_grid_buy_price - current_grid_sell_price) / max(current_grid_buy_price, 1e-6))

    # Energy constraints
    min_energy = battery_capacity_kwh * 0.05
    max_energy = battery_capacity_kwh * 0.95
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)
    available_to_charge = max(0, max_energy - current_energy_stored_kwh)

    # Evaluate actions
    charge_rate = self.evaluate_charge_action(current_soc, spread, volatility, momentum, available_to_charge)
    discharge_rate = self.evaluate_discharge_action(current_soc, spread, volatility, momentum, available_to_discharge)

    # Fast state machine: prioritize discharge slightly over charge for profitability
    if discharge_rate > 0.5 and (self.state != "CHARGING" or self.state_age > self.min_state_duration):
      self.state = "DISCHARGING"
      self.state_age = 0
      return -discharge_rate

    elif charge_rate > 0.5 and (self.state != "DISCHARGING" or self.state_age > self.min_state_duration):
      self.state = "CHARGING"
      self.state_age = 0
      return charge_rate

    else:
      self.state = "NEUTRAL"
      self.state_age += 1
      return 0.0
