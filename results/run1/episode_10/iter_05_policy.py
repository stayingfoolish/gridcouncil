class Policy:
  def __init__(self):
    self.price_history = []
    self.demand_history = []
    self.last_action = 0.0
    self.max_history_length = 20
    self.min_discharge_soc = 0.15  # Aggressive floor for discharge
    self.target_charge_soc = 0.85  # Pre-position for peak selling
    self.price_momentum_threshold = 0.001
    self.aggressive_sell_price = 0.32  # Lower threshold than before (0.38)
    self.aggressive_buy_price = 0.12  # Lower threshold (0.15)

  def _calculate_price_trend(self):
    """Return slope of recent price trend (positive = rising)."""
    if len(self.price_history) < 3:
      return 0
    recent = self.price_history[-3:]
    slope = (recent[-1] - recent[0]) / 2
    return slope

  def _identify_peak_window(self):
    """Detect if we're in a high-price window or approaching one."""
    if len(self.price_history) < 2:
      return False
    current_price = self.price_history[-1]
    recent_avg = sum(self.price_history[-5:]) / min(5, len(self.price_history))
    return current_price > recent_avg * 1.15

  def _calculate_low_window(self):
    """Detect if we're in a low-price buying opportunity."""
    if len(self.price_history) < 2:
      return False
    current_price = self.price_history[-1]
    recent_avg = sum(self.price_history[-5:]) / min(5, len(self.price_history))
    return current_price < recent_avg * 0.90

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

    # Emergency demand response (unchanged)
    demand_shortfall = max(0.0, current_demand_kw - current_pv_generation_kw)
    if demand_shortfall > 3.5 and current_soc > 0.20:
      return -min(7.0, current_energy_stored_kwh * 0.25)

    action_kw = 0.0

    price_trend = self._calculate_price_trend()
    in_peak_window = self._identify_peak_window()
    in_low_window = self._calculate_low_window()

    # AGGRESSIVE DISCHARGE: High sell prices = revenue opportunity
    if in_peak_window and current_soc > self.min_discharge_soc:
      if current_grid_sell_price > self.aggressive_sell_price:
        # Discharge aggressively during peak
        if current_soc > 0.70:
          action_kw = -8.0
        elif current_soc > 0.50:
          action_kw = -5.0
        else:
          action_kw = -2.5

      # Pre-discharge if price momentum is upward (prepare for even higher peak)
      elif price_trend > self.price_momentum_threshold and current_soc > 0.75:
        action_kw = -4.0

    # AGGRESSIVE CHARGE: Low buy prices = cost reduction opportunity
    elif in_low_window and current_soc < self.target_charge_soc:
      if current_grid_buy_price < self.aggressive_buy_price:
        if current_soc < 0.50:
          action_kw = 9.0
        elif current_soc < 0.70:
          action_kw = 7.0
        else:
          action_kw = 4.0

      # Pre-charge if price momentum is downward (prepare for lower prices)
      elif price_trend < -self.price_momentum_threshold and current_soc < 0.60:
        action_kw = 6.0

    # DEFAULT: Harvest excess PV whenever possible
    else:
      excess_pv = max(0.0, current_pv_generation_kw - current_demand_kw)
      if excess_pv > 1.0 and current_soc < 0.80:
        action_kw = min(5.0, excess_pv)
      # Lightweight discharge if overcharged and sell price reasonable
      elif current_soc > 0.85 and current_grid_sell_price > 0.25:
        action_kw = -2.0

    # Softer smoothing (allow sharper turns for opportunity capture)
    if self.last_action * action_kw > 0:
      action_kw *= 0.80

    # Physical constraints
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh
    if action_kw > 0:
      action_kw = min(action_kw, available_capacity / 0.5)
    else:
      action_kw = max(action_kw, -current_energy_stored_kwh / 0.5)

    self.last_action = action_kw
    return max(-8.0, min(10.0, action_kw))
