import math

class Policy:
  def __init__(self):
    """Cost-optimized predictive battery management with adaptive learning."""
    self.price_history = []
    self.max_history_length = 72  # 3 days for better pattern learning

    # Predictive model parameters
    self.prediction_window = 8  # Look ahead 8 hours
    self.recent_actions = []
    self.action_outcomes = []  # (action, cost_delta) pairs for learning
    self.max_outcomes_history = 50

    # Adaptive thresholds learned from outcomes
    self.learned_charge_premium = 0.15  # How much we benefit from charging early
    self.learned_discharge_premium = 0.12  # How much we benefit from discharging early
    self.learning_rate = 0.05

    # Current regime state
    self.last_regime = 'initialization'
    self.regime_confidence = 0.0

  def _predict_price_trend(self, prices):
    """Predict next 8 hours using polynomial regression on recent data."""
    if len(prices) < 12:
      return 0.0, 0.0  # (slope, curvature)

    # Use last 24 hours (stronger signal than 8)
    recent_prices = prices[-24:] if len(prices) >= 24 else prices

    # Calculate linear trend
    n = len(recent_prices)
    avg_idx = n / 2.0
    avg_price = sum(recent_prices) / n

    numerator = sum((i - avg_idx) * (recent_prices[i] - avg_price) for i in range(n))
    denominator = sum((i - avg_idx) ** 2 for i in range(n))
    slope = numerator / denominator if denominator > 0 else 0.0

    # Calculate curvature (acceleration)
    if n >= 6:
      first_half = sum(recent_prices[:n//2]) / (n//2)
      last_half = sum(recent_prices[n//2:]) / (n - n//2)
      mid_point = sum(recent_prices[n//4:3*n//4]) / (n//2)
      curvature = (first_half + last_half - 2 * mid_point)
    else:
      curvature = 0.0

    return slope, curvature

  def _estimate_future_price_range(self, prices, slope, curvature):
    """Estimate min/max prices in next 8 hours."""
    if not prices:
      return prices[-1] if prices else 0, prices[-1] if prices else 0

    current_price = prices[-1]

    # Extrapolate 8 periods forward
    future_high = current_price + (slope * 8) + (curvature * 8)
    future_low = current_price + (slope * 8) - abs(curvature * 4)

    # Bound by volatility envelope (avoid extreme predictions)
    recent_std = self._calculate_volatility(prices[-24:] if len(prices) >= 24 else prices)
    max_move = recent_std * 3

    future_high = min(future_high, current_price + max_move)
    future_low = max(future_low, current_price - max_move)

    return future_low, future_high

  def _calculate_volatility(self, prices):
    """Calculate standard deviation of prices."""
    if len(prices) < 2:
      return 0.0
    avg = sum(prices) / len(prices)
    variance = sum((p - avg) ** 2 for p in prices) / len(prices)
    return math.sqrt(variance)

  def _update_learning_from_action(self, action_kw, sell_price_at_action, current_cost):
    """Learn from recent action outcomes to adjust premiums."""
    if len(self.action_outcomes) >= self.max_outcomes_history:
      self.action_outcomes.pop(0)

    self.action_outcomes.append((action_kw, current_cost))

    if len(self.action_outcomes) >= 5:
      # Simple: if recent actions reduced cost, increase premium; else decrease
      recent_costs = [cost for _, cost in self.action_outcomes[-5:]]
      cost_trend = recent_costs[-1] - recent_costs[0]

      if cost_trend < 0:  # Costs improving
        self.learned_charge_premium = min(0.30, self.learned_charge_premium + self.learning_rate * 0.05)
        self.learned_discharge_premium = min(0.25, self.learned_discharge_premium + self.learning_rate * 0.05)
      else:  # Costs worsening
        self.learned_charge_premium = max(0.05, self.learned_charge_premium - self.learning_rate * 0.05)
        self.learned_discharge_premium = max(0.03, self.learned_discharge_premium - self.learning_rate * 0.05)

  def _calculate_economic_thresholds(self, current_price, future_low, future_high, battery_level_ratio):
    """Calculate action thresholds based on economic opportunity."""
    price_range = future_high - future_low

    # CHARGE if future price significantly lower (buy low now, sell higher later)
    # Threshold: we charge if current price is high relative to predicted future low
    charge_threshold = future_low * (1.0 + self.learned_charge_premium)
    charge_opportunity = current_price - charge_threshold

    # DISCHARGE if future price significantly higher
    discharge_threshold = future_high * (1.0 - self.learned_discharge_premium)
    discharge_opportunity = discharge_threshold - current_price

    return charge_opportunity, discharge_opportunity

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Cost-optimized predictive control."""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)

    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    action_kw = 0.0

    # CORE LOGIC: Predict and exploit
    if len(self.price_history) >= 12:
      slope, curvature = self._predict_price_trend(self.price_history)
      future_low, future_high = self._estimate_future_price_range(self.price_history, slope, curvature)
      charge_opp, discharge_opp = self._calculate_economic_thresholds(
        current_grid_buy_price, future_low, future_high, battery_level_ratio
      )

      # CHARGE: If current price is good for buying and battery has room
      if charge_opp > 0.01 and battery_level_ratio < 0.95:
        # Charge more aggressively when opportunity is larger
        confidence = min(0.95, 0.4 + charge_opp * 50)
        available_cap = battery_capacity_kwh - current_energy_stored_kwh
        action_kw = min(14.0, 8.0 + available_cap * 0.05) * confidence

      # DISCHARGE: If current price is good for selling and battery has charge
      elif discharge_opp > 0.01 and battery_level_ratio > 0.15:
        # Discharge more aggressively when opportunity is larger
        confidence = min(0.95, 0.4 + discharge_opp * 50)
        available_discharge = current_energy_stored_kwh
        action_kw = -min(9.5, 6.0 + available_discharge * 0.04) * confidence

      # Update learning
      self._update_learning_from_action(action_kw, current_grid_sell_price,
                                       current_grid_buy_price * current_energy_stored_kwh)

    return action_kw
