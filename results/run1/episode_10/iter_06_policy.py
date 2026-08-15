class Policy:
  def __init__(self):
    self.price_history = []
    self.demand_history = []
    self.last_action = 0.0
    self.max_history_length = 30

    # Multi-scale momentum detection
    self.trend_short = 0.0      # 3-step trend
    self.trend_medium = 0.0     # 7-step trend
    self.volatility_window = 10  # Recent price std dev

    # Dynamic threshold calculation
    self.base_sell_threshold = 0.28
    self.base_buy_threshold = 0.10
    self.volatility_multiplier = 1.2

    # SOC-based discharge targets
    self.critical_soc = 0.25     # Hard floor before emergency charge
    self.aggressive_discharge_soc = 0.70
    self.conservative_charge_soc = 0.40
    self.safety_charge_soc = 0.85

  def _calculate_volatility(self):
    """Calculate price volatility to adjust thresholds dynamically."""
    if len(self.price_history) < self.volatility_window:
      return 0.05
    recent = self.price_history[-self.volatility_window:]
    mean = sum(recent) / len(recent)
    variance = sum((p - mean)**2 for p in recent) / len(recent)
    return variance**0.5

  def _calculate_multi_scale_trend(self):
    """Detect price momentum across multiple timeframes."""
    short_slope = 0
    medium_slope = 0

    if len(self.price_history) >= 3:
      recent3 = self.price_history[-3:]
      short_slope = (recent3[-1] - recent3[0]) / 2.0

    if len(self.price_history) >= 7:
      recent7 = self.price_history[-7:]
      medium_slope = (recent7[-1] - recent7[0]) / 6.0

    return short_slope, medium_slope

  def _is_strong_uptrend(self, short_slope, medium_slope):
    """Strong uptrend = both slopes positive and aligned."""
    return short_slope > 0.002 and medium_slope > 0.001

  def _is_strong_downtrend(self, short_slope, medium_slope):
    """Strong downtrend = both slopes negative and aligned."""
    return short_slope < -0.002 and medium_slope < -0.001

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Emergency response unchanged
    demand_shortfall = max(0.0, current_demand_kw - current_pv_generation_kw)
    if demand_shortfall > 3.5 and current_soc > 0.20:
      return -min(7.0, current_energy_stored_kwh * 0.25)

    action_kw = 0.0

    # Calculate dynamic thresholds
    volatility = self._calculate_volatility()
    dynamic_sell_price = self.base_sell_threshold + (volatility * self.volatility_multiplier)
    dynamic_buy_price = self.base_buy_threshold + (volatility * 0.5)

    short_slope, medium_slope = self._calculate_multi_scale_trend()
    strong_up = self._is_strong_uptrend(short_slope, medium_slope)
    strong_down = self._is_strong_downtrend(short_slope, medium_slope)

    # OVERCHARGE DISCHARGE MODE: If battery high, sell aggressively
    if current_soc > self.aggressive_discharge_soc:
      if current_grid_sell_price > dynamic_sell_price * 0.95:
        # Overcharged = high priority to discharge
        if current_soc > 0.85:
          action_kw = -9.0
        elif current_soc > 0.75:
          action_kw = -7.0
        else:
          action_kw = -5.0
      # Also discharge if strong uptrend detected (prices rising, sell soon)
      elif strong_up and current_soc > 0.75:
        action_kw = -6.0

    # OPPORTUNISTIC CHARGE MODE: Buy when prices are low
    elif strong_down and current_soc < self.conservative_charge_soc:
      # Strong downtrend = good time to charge
      if current_soc < 0.40:
        action_kw = 10.0
      elif current_soc < 0.60:
        action_kw = 8.0
      else:
        action_kw = 5.0

    elif current_grid_buy_price < dynamic_buy_price and current_soc < self.safety_charge_soc:
      # Cheap price = charge based on SOC
      if current_soc < 0.50:
        action_kw = 9.0
      else:
        action_kw = 6.0

    # HARVEST PV: Default mode
    else:
      excess_pv = max(0.0, current_pv_generation_kw - current_demand_kw)
      if excess_pv > 1.0 and current_soc < 0.80:
        action_kw = min(5.0, excess_pv)
      # Opportunistic low discharge if overcharged and sell price acceptable
      elif current_soc > 0.80 and current_grid_sell_price > 0.22:
        action_kw = -3.0

    # Reduced smoothing to allow faster response
    if self.last_action != 0 and self.last_action * action_kw > 0:
      action_kw *= 0.70

    # Physical constraints
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh
    if action_kw > 0:
      action_kw = min(action_kw, available_capacity / 0.5)
    else:
      action_kw = max(action_kw, -current_energy_stored_kwh / 0.5)

    self.last_action = action_kw
    return max(-9.0, min(10.0, action_kw))
