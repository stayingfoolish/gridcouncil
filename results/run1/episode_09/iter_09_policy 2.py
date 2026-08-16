class Policy:
  def __init__(self):
    self.price_history = []
    self.action_history = []
    self.performance_window = 50  # Increased from 20 for stability
    self.adaptation_rate = 0.08   # Decreased from 0.15 to prevent drift

    # Mean-reversion trading thresholds (standard deviations from mean)
    self.charge_std_threshold = 0.75    # Buy when price ≤ 0.75 std below mean
    self.discharge_std_threshold = 0.95 # Sell when price ≥ 0.95 std above mean
    self.min_profitable_margin = 0.06   # Arbitrage when spread > 6% of mean

    # Aggressive action sizing (currently 80% and limited by space/soc)
    self.base_charge_rate = 0.85        # Use 85% of MAX_CHARGE capacity
    self.base_discharge_rate = 0.90     # Use 90% of MAX_DISCHARGE capacity

    # SOC management: keep battery in active trading zone
    self.soc_target_low = 0.35          # Don't let battery drop below this
    self.soc_target_high = 0.70         # Don't let battery sit above this
    self.min_action_kw = 0.08           # Lower threshold enables more trades

  def get_price_statistics(self, price_history, window=35):
    """Calculate mean and standard deviation of recent prices"""
    if len(price_history) < window:
      recent = price_history[-10:] if len(price_history) >= 10 else price_history
    else:
      recent = price_history[-window:]

    if len(recent) < 2:
      return price_history[-1] if price_history else 0, 0.01

    mean = sum(recent) / len(recent)
    variance = sum((p - mean) ** 2 for p in recent) / len(recent)
    std_dev = max(variance ** 0.5, 0.01)  # Avoid division by zero
    return mean, std_dev

  def get_volatility_multiplier(self, std_dev, mean):
    """Scale action size based on volatility - higher volatility = more opportunity"""
    if mean < 0.01:
      return 1.0
    coefficient_of_variation = std_dev / mean
    # When CV > 10% (high volatility), boost aggression up to 1.4x
    volatility_boost = min(coefficient_of_variation * 3.5, 0.4)
    return 1.0 + volatility_boost

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    MAX_CHARGE = 10.0
    MAX_DISCHARGE = 8.0

    soc = current_energy_stored_kwh / battery_capacity_kwh

    # Critical safety guardrails
    if soc < 0.05:
      return min(MAX_CHARGE, battery_capacity_kwh - current_energy_stored_kwh)
    if soc > 0.95:
      return -min(MAX_DISCHARGE, current_energy_stored_kwh)

    # Track price history
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > 96:
      self.price_history.pop(0)

    # Get current price statistics
    buy_mean, buy_std = self.get_price_statistics(self.price_history, window=35)

    # Normalize price deviations
    buy_deviation = (current_grid_buy_price - buy_mean) / (buy_std + 0.001)
    sell_deviation = (current_grid_sell_price - buy_mean) / (buy_std + 0.001)

    # Calculate profit margin
    margin_percentage = (current_grid_sell_price - current_grid_buy_price) / (buy_mean + 0.001)
    volatility_multiplier = self.get_volatility_multiplier(buy_std, buy_mean)

    action = 0.0

    # PRIMARY STRATEGY: MEAN-REVERSION TRADING
    # Buy when price is significantly below its rolling mean
    if buy_deviation < -self.charge_std_threshold and soc < 0.85:
      space = battery_capacity_kwh - current_energy_stored_kwh
      # Intensity based on how far below mean (higher deviation = more aggressive)
      intensity = min(1.0, abs(buy_deviation) / 2.5)
      charge = MAX_CHARGE * self.base_charge_rate * intensity * volatility_multiplier
      charge = min(charge, space)
      if charge > self.min_action_kw:
        action = charge

    # Sell when price is significantly above its rolling mean
    elif sell_deviation > self.discharge_std_threshold and soc > 0.15:
      intensity = min(1.0, abs(sell_deviation) / 2.5)
      discharge = MAX_DISCHARGE * self.base_discharge_rate * intensity * volatility_multiplier
      discharge = min(discharge, current_energy_stored_kwh)
      if discharge > self.min_action_kw:
        action = -discharge

    # SECONDARY STRATEGY: MARGIN ARBITRAGE (when deviation signals are weak)
    # This captures trades when buy-sell spread widens, regardless of absolute levels
    elif margin_percentage > self.min_profitable_margin and abs(buy_deviation) < 1.0:
      margin_intensity = min(1.0, margin_percentage / 0.15)  # Normalize to 15% spread

      if soc < self.soc_target_high and current_grid_buy_price < buy_mean * 1.05:
        # Buy cheaply when we have sell upside
        space = battery_capacity_kwh - current_energy_stored_kwh
        charge = MAX_CHARGE * 0.50 * margin_intensity * (0.5 + volatility_multiplier * 0.3)
        charge = min(charge, space)
        if charge > self.min_action_kw:
          action = charge

      elif soc > self.soc_target_low and current_grid_sell_price > buy_mean * 0.95:
        # Sell at premium when we have cheap entry
        discharge = MAX_DISCHARGE * 0.50 * margin_intensity * (0.5 + volatility_multiplier * 0.3)
        discharge = min(discharge, current_energy_stored_kwh)
        if discharge > self.min_action_kw:
          action = -discharge

    # TERTIARY: DEMAND RESPONSE with SOC management
    elif current_demand_kw > current_pv_generation_kw + 2.0 and soc > 0.28:
      if soc > self.soc_target_high:
        discharge = min(MAX_DISCHARGE * 0.35, current_energy_stored_kwh)
        if discharge > self.min_action_kw:
          action = -discharge

    # Record action
    action_record = {
      'action': action,
      'soc': soc,
      'buy_price': current_grid_buy_price,
      'sell_price': current_grid_sell_price,
      'buy_deviation': buy_deviation,
      'margin': margin_percentage,
      'timestamp': len(self.price_history)
    }
    self.action_history.append(action_record)
    if len(self.action_history) > self.performance_window * 3:
      self.action_history.pop(0)

    return action
