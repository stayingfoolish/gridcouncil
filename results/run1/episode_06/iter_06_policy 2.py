class Policy:
  def __init__(self):
    self.price_history = []
    self.max_history = 48
    self.battery_degradation_cost = 0.001

    # Conservative thresholds
    self.arbitrage_threshold = 0.05  # 5% spread required for any trade
    self.min_profitable_spread = 0.035  # 3.5% net profit after degradation (2 cycles @ 0.1% each)
    self.soc_target = 0.50
    self.soc_deadband = 0.10  # Don't trade if within ±10% of target

    # Volatility regime tracking
    self.volatility_history = []
    self.max_vol_history = 12
    self.high_vol_threshold = 0.08  # 8% coefficient of variation = high volatility

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

    # Track price history
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # EMERGENCY BOUNDARIES - unchanged
    if soc < 0.05:
      return 12.0
    if soc > 0.98:
      return -10.0

    # PV BALANCE HANDLING - prioritize physical flows
    if pv_balance > 2.5 and soc < 0.80:
      return min(12.0, pv_balance * 1.2)
    if pv_balance < -2.5 and soc > 0.20:
      return max(-10.0, pv_balance * 1.2)

    # NOVEL: Calculate regime and arbitrage opportunity
    vol = self._calculate_volatility(self.price_history[-6:]) if len(self.price_history) >= 6 else 0
    self.volatility_history.append(vol)
    if len(self.volatility_history) > self.max_vol_history:
      self.volatility_history.pop(0)

    avg_vol = sum(self.volatility_history) / len(self.volatility_history) if self.volatility_history else 0
    is_high_volatility = vol > self.high_vol_threshold

    # Calculate current spread opportunity
    spread = (current_grid_buy_price - current_grid_sell_price) / current_grid_buy_price
    net_profit_opportunity = spread - (self.battery_degradation_cost * 2 * 100)

    # ARBITRAGE LOGIC - only trade on clear spreads
    if net_profit_opportunity > self.min_profitable_spread and not is_high_volatility:
      # Current price is favorable for buying (sell price low relative to buy price)
      if soc < (self.soc_target - self.soc_deadband) and soc < 0.85:
        return 6.0  # Modest buy, confident arb opportunity
      elif soc > (self.soc_target + self.soc_deadband) and soc > 0.20:
        return -6.0  # Modest sell, confident arb opportunity

    # VOLATILITY REFUGE - in high-vol regimes, focus on SOC management only
    if is_high_volatility:
      if soc > (self.soc_target + 0.15):
        return -2.0  # Gentle discharge toward safety
      elif soc < (self.soc_target - 0.15):
        return 2.0
      return 0.0  # Stay still in uncertain times

    # DEFAULT: SOC REBALANCING (very conservative)
    if soc > (self.soc_target + 0.20):
      return -1.5
    elif soc < (self.soc_target - 0.20):
      return 1.5

    return 0.0

  def _calculate_volatility(self, prices: list) -> float:
    if len(prices) < 2:
      return 0.0
    avg = sum(prices) / len(prices)
    variance = sum((p - avg) ** 2 for p in prices) / len(prices)
    std_dev = variance ** 0.5
    return std_dev / (avg + 1e-6)
