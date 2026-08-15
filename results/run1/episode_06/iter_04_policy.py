class Policy:
  def __init__(self):
    self.buy_price_ma = None
    self.sell_price_ma = None
    self.buy_price_long_ma = None
    self.sell_price_long_ma = None
    self.price_history = []
    self.volatility = 0.0

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

    self.price_history.append((current_grid_buy_price, current_grid_sell_price))
    if len(self.price_history) > 20:
      self.price_history.pop(0)

    if len(self.price_history) > 1:
      buy_prices = [p[0] for p in self.price_history]
      avg_price = sum(buy_prices) / len(buy_prices)
      variance = sum((p - avg_price) ** 2 for p in buy_prices) / len(buy_prices)
      self.volatility = (variance ** 0.5) / avg_price if avg_price > 0 else 0.0

    if self.buy_price_ma is None:
      self.buy_price_ma = current_grid_buy_price
      self.sell_price_ma = current_grid_sell_price
    else:
      vol_weight = min(0.4 + self.volatility * 0.3, 0.8)
      self.buy_price_ma = (1 - vol_weight) * self.buy_price_ma + vol_weight * current_grid_buy_price
      self.sell_price_ma = (1 - vol_weight) * self.sell_price_ma + vol_weight * current_grid_sell_price

    if self.buy_price_long_ma is None:
      self.buy_price_long_ma = current_grid_buy_price
      self.sell_price_long_ma = current_grid_sell_price
    else:
      self.buy_price_long_ma = 0.9 * self.buy_price_long_ma + 0.1 * current_grid_buy_price
      self.sell_price_long_ma = 0.9 * self.sell_price_long_ma + 0.1 * current_grid_sell_price

    if soc < 0.08:
      return 12.0
    if soc > 0.97:
      return -10.0

    if pv_balance > 1.0 and soc < 0.90:
      return min(12.0, pv_balance * 2.0)
    if pv_balance < -1.0 and soc > 0.10:
      return max(-10.0, pv_balance * 2.0)

    regime = self._detect_regime()

    if regime == "trending_up":
      buy_discount = (self.buy_price_long_ma - current_grid_buy_price) / self.buy_price_long_ma if self.buy_price_long_ma > 0 else 0
      if buy_discount > 0.01 and soc < 0.70:
        action = 6.0 + (0.70 - soc) * 4.0
        return min(10.0, max(0.0, action))

    elif regime == "trending_down":
      sell_premium = (current_grid_sell_price - self.sell_price_long_ma) / self.sell_price_long_ma if self.sell_price_long_ma > 0 else 0
      if sell_premium > 0.01 and soc > 0.30:
        action = -6.0 - (soc - 0.30) * 4.0
        return max(-10.0, min(0.0, action))

    elif regime == "mean_reverting":
      buy_discount = (self.buy_price_long_ma - current_grid_buy_price) / self.buy_price_long_ma if self.buy_price_long_ma > 0 else 0
      sell_premium = (current_grid_sell_price - self.sell_price_long_ma) / self.sell_price_long_ma if self.sell_price_long_ma > 0 else 0

      if buy_discount > 0.04 and soc < 0.85:
        action = 8.0 + buy_discount * 20.0
        return min(12.0, max(0.0, action))

      if sell_premium > 0.04 and soc > 0.15:
        action = -8.0 - sell_premium * 20.0
        return max(-10.0, min(0.0, action))

    elif regime == "stable":
      buy_discount = (self.buy_price_long_ma - current_grid_buy_price) / self.buy_price_long_ma if self.buy_price_long_ma > 0 else 0
      sell_premium = (current_grid_sell_price - self.sell_price_long_ma) / self.sell_price_long_ma if self.sell_price_long_ma > 0 else 0

      if buy_discount > 0.02 and soc < 0.75:
        return 4.0
      if sell_premium > 0.02 and soc > 0.25:
        return -4.0

    if soc > 0.55:
      return -1.0
    elif soc < 0.45:
      return 1.0

    return 0.0

  def _detect_regime(self) -> str:
    if self.buy_price_ma is None or self.buy_price_long_ma is None:
      return "stable"

    if self.buy_price_long_ma == 0:
      return "stable"

    ma_diff = (self.buy_price_ma - self.buy_price_long_ma) / self.buy_price_long_ma

    if ma_diff > 0.02:
      return "trending_up"
    elif ma_diff < -0.02:
      return "trending_down"
    elif self.volatility > 0.15:
      return "mean_reverting"
    else:
      return "stable"
