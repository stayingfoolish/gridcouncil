class Policy:
  def __init__(self):
    self.price_history = []
    self.max_history_length = 50

    # Statistical windows
    self.mean_window = 20  # Rolling mean period
    self.momentum_window = 5  # Short momentum check

    # Hard boundaries only
    self.min_soc = 0.15  # Emergency floor
    self.max_soc = 0.95  # Safety ceiling

    # Decision thresholds (z-score based)
    self.buy_zscore = -0.7   # Buy when 0.7σ below mean
    self.sell_zscore = 0.7   # Sell when 0.7σ above mean
    self.neutral_band = 0.2  # Zone of indifference

    # Power scaling
    self.base_charge = 8.5
    self.base_discharge = 7.5
    self.momentum_boost = 1.4  # Amplify when momentum agrees

  def _calculate_statistics(self):
    """Calculate mean, std, z-score, and momentum."""
    if len(self.price_history) < self.mean_window:
      return None, None, None, None

    recent = self.price_history[-self.mean_window:]
    mean = sum(recent) / len(recent)
    var = sum((p - mean)**2 for p in recent) / len(recent)
    std = var**0.5

    if std < 0.001:  # No volatility = no signal
      return mean, std, 0, 0

    zscore = (self.price_history[-1] - mean) / std

    # Momentum: is price rising or falling?
    if len(self.price_history) >= self.momentum_window:
      momentum = self.price_history[-1] - self.price_history[-self.momentum_window]
    else:
      momentum = 0

    return mean, std, zscore, momentum

  def take_action(self, current_energy_stored_kwh, current_pv_generation_kw,
                  current_demand_kw, current_grid_buy_price,
                  current_grid_sell_price, battery_capacity_kwh):

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Emergency demand response (unchanged)
    demand_shortfall = max(0.0, current_demand_kw - current_pv_generation_kw)
    if demand_shortfall > 3.5 and current_soc > 0.20:
      return -min(7.0, current_energy_stored_kwh * 0.25)

    # Hard boundaries override all else
    if current_soc < self.min_soc:
      return 9.5  # Maximum charge
    if current_soc > self.max_soc:
      return -8.5  # Maximum discharge

    # Need sufficient history for statistical decision
    mean, std, zscore, momentum = self._calculate_statistics()
    if mean is None:
      return 0.0

    if std < 0.001:  # No price volatility = no signal
      return 0.0

    action_kw = 0.0

    # PRIMARY STRATEGY: Statistical mean reversion
    if zscore < self.buy_zscore and current_soc < 0.85:
      # Price is significantly below mean → BUY
      action_kw = self.base_charge
      if momentum < 0:  # Downtrend reinforces: price will go lower → buy more
        action_kw *= self.momentum_boost

    elif zscore > self.sell_zscore and current_soc > 0.20:
      # Price is significantly above mean → SELL
      action_kw = -self.base_discharge
      if momentum > 0:  # Uptrend reinforces: price will go higher → sell more
        action_kw *= self.momentum_boost

    # SECONDARY: Opportunistic capture when battery extreme
    elif -self.neutral_band < zscore < self.neutral_band:
      # Neutral price zone: act only on battery state
      if current_soc > 0.80 and current_grid_sell_price > mean * 1.03:
        action_kw = -5.5  # High battery + acceptable sell price
      elif current_soc < 0.30 and current_grid_buy_price < mean * 0.98:
        action_kw = 7.5  # Low battery + cheap buy price

    # Physical constraints
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh
    if action_kw > 0:
      action_kw = min(action_kw, available_capacity / 0.5)
    else:
      action_kw = max(action_kw, -current_energy_stored_kwh / 0.5)

    return max(-9.0, min(10.0, action_kw))
