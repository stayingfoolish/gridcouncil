class Policy:
  def __init__(self):
    """Predictive market-positioned battery management"""
    self.price_history = []
    self.max_history = 48
    self.time_step = 0
    self.ema_fast = None   # 6-hour EMA
    self.ema_slow = None   # 24-hour EMA
    self.volatility_window = []
    self.max_volatility_samples = 24
    self.cycle_count = 0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Anticipate price direction and pre-position battery for arbitrage"""
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    # Track price and volatility
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    self.volatility_window.append(current_grid_buy_price)
    if len(self.volatility_window) > self.max_volatility_samples:
      self.volatility_window.pop(0)

    self.time_step += 1
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh

    # Calculate EMAs (simple approximation)
    ema_fast_alpha = 0.3   # 6-hour weight
    ema_slow_alpha = 0.1   # 24-hour weight

    if self.ema_fast is None:
      self.ema_fast = current_grid_buy_price
      self.ema_slow = current_grid_buy_price
    else:
      self.ema_fast = ema_fast_alpha * current_grid_buy_price + (1 - ema_fast_alpha) * self.ema_fast
      self.ema_slow = ema_slow_alpha * current_grid_buy_price + (1 - ema_slow_alpha) * self.ema_slow

    # Calculate trend (positive = rising prices)
    trend = self.ema_fast - self.ema_slow
    trend_strength = abs(trend) / (self.ema_slow + 0.01)

    # Calculate volatility
    if len(self.volatility_window) > 1:
      mean_price = sum(self.volatility_window) / len(self.volatility_window)
      variance = sum((p - mean_price) ** 2 for p in self.volatility_window) / len(self.volatility_window)
      volatility = (variance ** 0.5) / mean_price if mean_price > 0 else 0.05
    else:
      volatility = 0.05

    # Determine rate scaling factor based on volatility
    if volatility > 0.15:
      rate_scale = 0.5
    elif volatility < 0.05:
      rate_scale = 1.0
    else:
      rate_scale = 0.75

    # Determine dynamic SOC target based on trend
    if trend > 0.02:  # Rising prices
      target_soc = 0.35
      trend_bias = -1.0  # Prefer discharge
    elif trend < -0.02:  # Falling prices
      target_soc = 0.70
      trend_bias = 1.0   # Prefer charge
    else:  # Transition zone
      target_soc = 0.50
      trend_bias = 0.0

    # Adjust trend_bias by confidence
    trend_bias *= min(trend_strength / 0.05, 1.2)

    current_spread = current_grid_sell_price - current_grid_buy_price
    cycle_cost = 0.02  # $/kWh
    min_profitable_spread = cycle_cost * 2 / (max_discharge_rate + max_charge_rate)

    action_kw = 0.0

    # Primary: Trend-based positioning
    if abs(trend) > 0.02 and current_spread > min_profitable_spread:
      if trend > 0:  # Rising prices - discharge
        if soc_ratio > 0.30:
          action_kw = -max_discharge_rate * rate_scale * 0.8
          self.cycle_count += 1
      else:  # Falling prices - charge
        if soc_ratio < 0.80:
          action_kw = max_charge_rate * rate_scale * 0.8

    # Secondary: Zone-based mean reversion
    if action_kw == 0.0:
      if soc_ratio > target_soc + 0.10:
        action_kw = -max_discharge_rate * rate_scale * 0.5
      elif soc_ratio < target_soc - 0.10:
        action_kw = max_charge_rate * rate_scale * 0.5

    # Tertiary: PV integration (only when trend-aligned)
    if action_kw == 0.0:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 1.0 and (trend_bias >= 0 or soc_ratio < target_soc - 0.05):
        action_kw = min(pv_excess * 0.6, max_charge_rate * rate_scale * 0.4)
      elif current_demand_kw > current_pv_generation_kw + 1.0 and (trend_bias <= 0 or soc_ratio > target_soc + 0.05):
        action_kw = -min(current_demand_kw - current_pv_generation_kw, max_discharge_rate * rate_scale * 0.3)

    return action_kw
