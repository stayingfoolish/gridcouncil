class Policy:
  def __init__(self):
    self.max_discharge_rate = 10
    self.max_charge_rate = 12

    # Predictive window parameters
    self.lookahead_window = 4  # Use this actively
    self.lookback_window = 6   # Learn patterns from recent history
    self.price_history = []
    self.soc_history = []

    # Market regime parameters
    self.spike_threshold = 1.15  # Price is 15% above rolling avg = spike regime
    self.trough_threshold = 0.85  # Price is 15% below rolling avg = trough regime
    self.volatility_threshold = 0.12

    # Adaptive thresholds based on regime
    self.regime_soc_targets = {
      'spike': 0.15,      # Empty for selling at peak
      'trough': 0.85,     # Full for buying at bottom
      'stable': 0.50,     # Balanced
      'volatile': 0.60    # Slightly high to avoid trap trades
    }

    # Aggressiveness calibration
    self.utilization_boost = 1.3  # Increase from current conservative 1.0
    self.min_price_spread_for_trade = 0.08  # At least 8% spread to justify action

  def detect_market_regime(self, price_window):
    """Classify next N timesteps into market behavior"""
    if len(price_window) < 2:
      return 'stable'

    avg_price = sum(price_window) / len(price_window)
    volatility = sum(abs(p - avg_price) for p in price_window) / (avg_price * len(price_window)) if avg_price > 0 else 0

    if volatility > self.volatility_threshold:
      return 'volatile'

    # Detect trend: is price moving up (spike ahead) or down (trough ahead)?
    recent_change = (price_window[-1] - price_window[0]) / price_window[0] if price_window[0] > 0 else 0

    if recent_change > 0.10:
      return 'spike'
    elif recent_change < -0.10:
      return 'trough'
    else:
      return 'stable'

  def calculate_arbitrage_opportunity(self, current_buy_price, current_sell_price, future_prices):
    """Score: Can we buy now and sell later for profit?"""
    if not future_prices or len(future_prices) == 0:
      return 0

    future_sell_max = max(future_prices)
    profit_spread = (future_sell_max - current_buy_price) / current_buy_price if current_buy_price > 0 else 0

    # Only pursue if spread exceeds minimum viable margin
    return max(0, profit_spread - self.min_price_spread_for_trade) * 100

  def calculate_immediate_discharge_value(self, current_sell_price, historical_avg_buy_price):
    """Score: Is current sell price unusually high?"""
    if historical_avg_buy_price == 0:
      return 0

    discharge_value = (current_sell_price - historical_avg_buy_price) / historical_avg_buy_price
    return max(0, discharge_value - self.min_price_spread_for_trade) * 100

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    battery_fill_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    # Track history for regime learning
    self.price_history.append(current_grid_buy_price)
    self.soc_history.append(battery_fill_ratio)
    if len(self.price_history) > self.lookback_window:
      self.price_history.pop(0)
      self.soc_history.pop(0)

    # Regime detection using only historical prices (no forecasts)
    price_window = self.price_history[-self.lookahead_window:] if len(self.price_history) >= self.lookahead_window else self.price_history
    regime = self.detect_market_regime(price_window)
    target_soc = self.regime_soc_targets.get(regime, 0.50)

    # Historical average for context
    historical_avg_buy = sum(self.price_history) / len(self.price_history) if self.price_history else current_grid_buy_price

    # Score all strategic actions
    action_scores = {}

    # 1. CHARGE OPPORTUNITIES
    charge_score = 0

    # Regime-based positioning: move toward target SoC for upcoming regime
    if battery_fill_ratio < target_soc - 0.05:  # Below target by margin
      soc_gap_score = (target_soc - battery_fill_ratio) * 25
      charge_score = max(charge_score, soc_gap_score)

    # PV excess
    if current_pv_generation_kw > current_demand_kw + 1.0:
      excess_pv = current_pv_generation_kw - current_demand_kw
      pv_score = excess_pv * 12 * (0.95 - battery_fill_ratio) if battery_fill_ratio < 0.95 else 0
      charge_score = max(charge_score, pv_score)

    # Price opportunity: charge when price is low relative to historical average
    if current_grid_buy_price < historical_avg_buy * 0.90:
      price_advantage = (historical_avg_buy - current_grid_buy_price) / historical_avg_buy
      price_charge_score = price_advantage * 50 if battery_fill_ratio < 0.85 else 0
      charge_score = max(charge_score, price_charge_score)

    action_scores['charge'] = charge_score

    # 2. DISCHARGE OPPORTUNITIES
    discharge_score = 0

    # Peak-time discharge: sell now if price is high
    if current_grid_sell_price > historical_avg_buy * 1.15:
      peak_score = self.calculate_immediate_discharge_value(current_grid_sell_price, historical_avg_buy)
      discharge_score = max(discharge_score, peak_score)

    # Regime-based positioning: move toward target SoC
    if battery_fill_ratio > target_soc + 0.05:  # Above target by margin
      soc_gap_score = (battery_fill_ratio - target_soc) * 25
      discharge_score = max(discharge_score, soc_gap_score)

    # Supply support
    if current_demand_kw > current_pv_generation_kw + 2.0:
      deficit = current_demand_kw - current_pv_generation_kw
      supply_score = deficit * 10 if battery_fill_ratio > 0.25 else 0
      discharge_score = max(discharge_score, supply_score)

    action_scores['discharge'] = discharge_score

    # 3. HOLD
    action_scores['hold'] = 1.0  # Tiny baseline

    # Execute best action with higher utilization
    best_action = max(action_scores, key=action_scores.get)

    if best_action == 'charge' and action_scores['charge'] > 1.5:
      charge_power = min(
        self.max_charge_rate * self.utilization_boost,
        available_capacity * 0.9  # Use more of available space
      )
      charge_power = min(charge_power, max(0.5, action_scores['charge'] / 8))
      return max(0.5, charge_power)

    elif best_action == 'discharge' and action_scores['discharge'] > 1.5:
      discharge_power = min(
        self.max_discharge_rate * self.utilization_boost,
        current_energy_stored_kwh * 0.8  # Discharge more aggressively
      )
      discharge_power = min(discharge_power, max(0.5, action_scores['discharge'] / 8))
      return -max(0.5, discharge_power)

    return 0.0
