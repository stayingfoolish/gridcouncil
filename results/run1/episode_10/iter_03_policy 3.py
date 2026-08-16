class Policy:
  def __init__(self):
    """Initializes the policy with price history tracking."""
    self.price_history = []
    self.demand_history = []
    self.last_action = 0.0
    self.max_history_length = 10

  def _calculate_price_trend(self):
    """Estimate price direction: -1 (falling), 0 (stable), 1 (rising)"""
    if len(self.price_history) < 3:
      return 0
    recent_avg = sum(self.price_history[-3:]) / 3
    older_avg = sum(self.price_history[-6:-3]) / 3 if len(self.price_history) >= 6 else recent_avg
    diff = recent_avg - older_avg
    if abs(diff) < 0.01:
      return 0
    return 1 if diff > 0 else -1

  def _calculate_demand_intensity(self):
    """Estimate current demand level relative to history: 0-1 scale"""
    if len(self.demand_history) < 3:
      return 0.5
    avg_demand = sum(self.demand_history) / len(self.demand_history)
    if avg_demand == 0:
      return 0.5
    return min(1.0, self.demand_history[-1] / (avg_demand * 1.5))

  def _calculate_target_soc(self, buy_price, sell_price, price_trend, demand_intensity):
    """Calculate optimal battery target level based on market conditions."""
    price_spread = sell_price - buy_price

    # Base target: 50% SOC for balanced operation
    base_target = 0.50

    # Adjust for price trend: higher SOC when prices rising, lower when falling
    trend_adjustment = price_trend * 0.15

    # Adjust for demand: higher SOC when demand is high (insurance against peak demand)
    demand_adjustment = (demand_intensity - 0.5) * 0.20

    # Adjust for price spread opportunity: higher SOC when spread is wide (more profit opportunity)
    spread_factor = min(0.3, max(-0.2, (price_spread - 0.15) * 0.5))

    target = base_target + trend_adjustment + demand_adjustment + spread_factor
    return max(0.15, min(0.85, target))

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines battery action based on target SOC and price trends."""

    # Update history
    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Calculate driving factors
    price_trend = self._calculate_price_trend()
    demand_intensity = self._calculate_demand_intensity()
    target_soc = self._calculate_target_soc(
      current_grid_buy_price,
      current_grid_sell_price,
      price_trend,
      demand_intensity
    )

    # Immediate demand coverage (override)
    demand_shortfall = max(0.0, current_demand_kw - current_pv_generation_kw)
    if demand_shortfall > 2.0 and current_soc > 0.20:
      return -min(5.0, current_energy_stored_kwh * 0.15)

    # Smart SOC targeting with hysteresis
    soc_error = target_soc - current_soc

    action_kw = 0.0

    if soc_error > 0.10:  # Need to charge
      # Aggressive charging when prices low or trend rising, SOC well below target
      if current_grid_buy_price < 0.12 or (price_trend >= 0 and current_soc < target_soc - 0.15):
        action_kw = 9.0
      elif current_grid_buy_price < 0.18:
        action_kw = 6.0
      else:
        # Charge from PV excess
        excess_pv = max(0.0, current_pv_generation_kw - current_demand_kw)
        if excess_pv > 1.0:
          action_kw = min(4.0, excess_pv)

    elif soc_error < -0.10:  # Need to discharge
      # Aggressive discharge when prices high or trend falling, SOC well above target
      if current_grid_sell_price > 0.40 or (price_trend <= 0 and current_soc > target_soc + 0.15):
        action_kw = -5.0
      elif current_grid_sell_price > 0.35:
        action_kw = -3.0

    # Damping: reduce action magnitude if previous action same direction (smooth transitions)
    if abs(action_kw) > 0 and self.last_action * action_kw > 0:
      action_kw *= 0.8

    # Clamp to physical limits
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh
    if action_kw > 0:
      action_kw = min(action_kw, available_capacity / 0.5)  # Rough charge time limit
    else:
      action_kw = max(action_kw, -current_energy_stored_kwh / 0.5)

    self.last_action = action_kw
    return max(-5.0, min(10.0, action_kw))
