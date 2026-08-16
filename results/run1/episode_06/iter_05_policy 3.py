class Policy:
  def __init__(self):
    self.price_history = []
    self.max_history = 48  # 2 days of hourly data
    self.momentum_lookback = 6  # 6 hours
    self.battery_degradation_cost = 0.001  # 0.1% per charge cycle
    self.min_profit_threshold = 0.02  # 2% min expected profit

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    soc = current_energy_stored_kwh / battery_capacity_kwh
    pv_balance = current_pv_generation_kw - current_demand_kw

    # Track price history with timestamps
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Emergency boundaries (unchanged)
    if soc < 0.05:
      return 12.0
    if soc > 0.98:
      return -10.0

    # PV surplus/deficit handling (unchanged)
    if pv_balance > 2.0 and soc < 0.85:
      return min(12.0, pv_balance * 1.5)
    if pv_balance < -2.0 and soc > 0.15:
      return max(-10.0, pv_balance * 1.5)

    # NOVEL: Predictive opportunity-based decision
    if len(self.price_history) >= self.momentum_lookback:
      buy_opp, sell_opp = self._calculate_opportunities(
        current_grid_buy_price, current_grid_sell_price
      )

      # Prefer opportunities over regimes
      action = self._select_action_by_opportunity(
        buy_opp, sell_opp, soc, current_grid_sell_price
      )

      if action != 0.0:
        return action

    # Fallback: Conservative SOC rebalancing
    if soc > 0.60:
      return -2.0
    elif soc < 0.40:
      return 2.0
    return 0.0

  def _calculate_opportunities(self, buy_price: float, sell_price: float) -> tuple:
    """Calculate buy/sell opportunity scores based on predicted price movement."""

    # Recent momentum (last 6 periods vs average)
    recent_prices = self.price_history[-self.momentum_lookback:]
    avg_price = sum(recent_prices) / len(recent_prices)
    momentum = (self.price_history[-1] - avg_price) / avg_price

    # Volatility trend (is market becoming more or less volatile?)
    vol_short = self._calculate_volatility(self.price_history[-6:])
    vol_long = self._calculate_volatility(self.price_history[-24:]) if len(self.price_history) >= 24 else vol_short
    vol_trend = (vol_short - vol_long) / (vol_long + 1e-6)

    # Predict next price direction
    momentum_weight = 0.4
    vol_weight = 0.3
    mean_reversion_weight = 0.3

    mean_price = sum(self.price_history) / len(self.price_history)
    mean_reversion_signal = (mean_price - self.price_history[-1]) / mean_price

    predicted_move = (
      momentum_weight * momentum +
      vol_weight * vol_trend +
      mean_reversion_weight * mean_reversion_signal * 0.1
    )

    # Calculate opportunity scores (expected profit %)
    buy_opportunity = max(0, -predicted_move * 100) - self.battery_degradation_cost * 100
    sell_opportunity = max(0, predicted_move * 100) - self.battery_degradation_cost * 100

    return buy_opportunity, sell_opportunity

  def _select_action_by_opportunity(self, buy_opp: float, sell_opp: float, soc: float, sell_price: float) -> float:
    """Select action based on opportunity scores and SOC constraints."""

    if buy_opp > self.min_profit_threshold and soc < 0.80:
      # Scale action by opportunity magnitude (higher opportunity = more aggressive)
      intensity = min(1.0, buy_opp / 10.0)  # Normalize to 10% max expected profit
      base_action = 5.0 + intensity * 6.0
      # Reduce action if SOC already high
      soc_factor = max(0.3, 1.0 - soc / 0.8)
      return min(12.0, base_action * soc_factor)

    elif sell_opp > self.min_profit_threshold and soc > 0.20:
      intensity = min(1.0, sell_opp / 10.0)
      base_action = -5.0 - intensity * 6.0
      # Reduce action if SOC already low
      soc_factor = max(0.3, soc / 0.8)
      return max(-10.0, base_action * soc_factor)

    return 0.0

  def _calculate_volatility(self, prices: list) -> float:
    """Calculate coefficient of variation for a price window."""
    if len(prices) < 2:
      return 0.0
    avg = sum(prices) / len(prices)
    variance = sum((p - avg) ** 2 for p in prices) / len(prices)
    std_dev = variance ** 0.5
    return std_dev / (avg + 1e-6)
