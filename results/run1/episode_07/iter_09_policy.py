class Policy:
  def __init__(self):
    """Predictive regime-based arbitrage system."""
    self.max_charge_rate = 12.0
    self.max_discharge_rate = 10.0

    # Price prediction buffers
    self.price_history = []
    self.history_length = 40  # Extended for trend analysis

    # Regime detection
    self.regime = "FLAT"
    self.regime_confidence = 0.5
    self.regime_age = 0

    # Predictive signals
    self.price_trend = 0  # -1, 0, 1
    self.price_acceleration = 0  # Is trend strengthening?
    self.predicted_next_price = None

    # Adaptive thresholds per regime
    self.regime_thresholds = {
      "MEAN_REVERTING": {"spread": 0.008, "charge_soc": 0.20, "discharge_soc": 0.80},
      "TRENDING": {"spread": 0.015, "charge_soc": 0.10, "discharge_soc": 0.90},
      "FLAT": {"spread": 0.025, "charge_soc": 0.40, "discharge_soc": 0.60}
    }

    self.min_state_duration = 1
    self.state = "NEUTRAL"
    self.state_age = 0

  def calculate_trend_strength(self):
    """Extract directional signal using dual moving averages."""
    if len(self.price_history) < 15:
      return 0, 0, 0

    prices = self.price_history
    ma_short = sum(prices[-5:]) / 5
    ma_long = sum(prices[-15:]) / 15

    trend = ma_short - ma_long
    trend_strength = abs(trend) / ma_long if ma_long > 0 else 0

    # Acceleration: is trend increasing?
    if len(prices) >= 20:
      prev_ma_short = sum(prices[-10:-5]) / 5
      prev_ma_long = sum(prices[-20:-5]) / 15
      prev_trend = prev_ma_short - prev_ma_long
      acceleration = (trend - prev_trend) / abs(prev_trend + 1e-6)
    else:
      acceleration = 0

    direction = 1 if trend > 0 else (-1 if trend < 0 else 0)
    return direction, min(trend_strength * 3, 1.0), acceleration

  def predict_next_price(self):
    """Simple linear extrapolation for next price."""
    if len(self.price_history) < 5:
      return self.price_history[-1] if self.price_history else None

    recent = self.price_history[-5:]
    slope = (recent[-1] - recent[0]) / 4  # 4-period change rate
    predicted = recent[-1] + slope
    return predicted

  def detect_regime(self, volatility, trend_strength, momentum_direction):
    """Classify current market regime."""
    if volatility > 0.08 and trend_strength < 0.15:
      new_regime = "MEAN_REVERTING"
    elif trend_strength > 0.08:
      new_regime = "TRENDING"
    else:
      new_regime = "FLAT"

    # Smooth regime transitions (require regime to persist 2+ periods)
    if new_regime != self.regime:
      self.regime_age = 0
    else:
      self.regime_age += 1
      if self.regime_age >= 2:
        self.regime = new_regime

  def calculate_volatility(self, prices):
    """Rolling volatility (coefficient of variation)."""
    if len(prices) < 3:
      return 0.01
    mean_price = sum(prices) / len(prices)
    if mean_price < 1e-6:
      return 0.01
    variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
    volatility = (variance ** 0.5) / mean_price
    return volatility

  def evaluate_charge_action(self, current_soc, spread, regime, trend, available_to_charge):
    """Charge decision with predictive and regime components."""
    if available_to_charge < 0.1:
      return 0.0

    config = self.regime_thresholds.get(regime, self.regime_thresholds["FLAT"])
    threshold = config["spread"]
    charge_soc_min = config["charge_soc"]

    if spread < threshold:
      return 0.0

    # Boost charging if prices predicted to rise (buy low before rise)
    trend_bonus = 1.0
    if trend < 0:  # Prices trending down—good entry
      trend_bonus = 1.4

    # Accelerate if SOC much below target
    energy_gap = max(0, (charge_soc_min - current_soc) * 100)
    rate = self.max_charge_rate * (0.5 + energy_gap / 150) * trend_bonus

    return min(rate, available_to_charge)

  def evaluate_discharge_action(self, current_soc, spread, regime, trend, available_to_discharge):
    """Discharge decision with predictive and regime components."""
    if available_to_discharge < 0.1:
      return 0.0

    config = self.regime_thresholds.get(regime, self.regime_thresholds["FLAT"])
    threshold = config["spread"]
    discharge_soc_max = config["discharge_soc"]

    if spread < threshold:
      return 0.0

    # Boost discharging if prices predicted to fall (sell high before fall)
    trend_bonus = 1.0
    if trend > 0:  # Prices trending up—good exit
      trend_bonus = 1.4

    # Accelerate if SOC much above target
    energy_gap = max(0, (current_soc - discharge_soc_max) * 100)
    rate = self.max_discharge_rate * (0.5 + energy_gap / 150) * trend_bonus

    return min(rate, available_to_discharge)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Main control loop with regime-based decisions."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Update price history
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.history_length:
      self.price_history.pop(0)

    # Calculate signals
    trend_direction, trend_strength, trend_accel = self.calculate_trend_strength()
    volatility = self.calculate_volatility(self.price_history)
    spread = max(0.0, (current_grid_buy_price - current_grid_sell_price) / max(current_grid_buy_price, 1e-6))

    # Detect regime
    self.detect_regime(volatility, trend_strength, trend_direction)

    # Energy constraints
    min_energy = battery_capacity_kwh * 0.05
    max_energy = battery_capacity_kwh * 0.95
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)
    available_to_charge = max(0, max_energy - current_energy_stored_kwh)

    # Evaluate actions
    charge_rate = self.evaluate_charge_action(current_soc, spread, self.regime, trend_direction, available_to_charge)
    discharge_rate = self.evaluate_discharge_action(current_soc, spread, self.regime, trend_direction, available_to_discharge)

    # State machine: prioritize signals
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
