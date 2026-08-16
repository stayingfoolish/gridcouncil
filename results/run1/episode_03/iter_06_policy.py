class Policy:
  def __init__(self):
    """Percentile-aware opportunity maximization strategy"""
    self.price_history = []
    self.max_history = 72  # 3-day rolling window
    self.percentile_bins = [0, 20, 35, 50, 65, 80, 100]
    self.state = 'HOLDING'  # CHARGING, DISCHARGING, HOLDING
    self.state_age = 0
    self.min_state_duration = 2  # Stay in state for minimum timesteps
    self.charge_threshold = 25  # Below 25th percentile = charge
    self.discharge_threshold = 75  # Above 75th percentile = discharge
    self.aggressive_charge_target = 0.30
    self.aggressive_discharge_target = 0.70

  def _get_price_percentile(self, current_price):
    """Calculate where current price ranks in recent history (0-100)"""
    if len(self.price_history) < 5:
      return 50
    sorted_prices = sorted(self.price_history)
    rank = sum(1 for p in sorted_prices if p < current_price) / len(sorted_prices) * 100
    return rank

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Opportunity-driven charging/discharging based on price percentiles"""
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    # Update price history
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    price_percentile = self._get_price_percentile(current_grid_buy_price)

    # State machine with hysteresis
    self.state_age += 1

    # State transitions (only allow transitions after min_state_duration)
    if self.state_age >= self.min_state_duration:
      if self.state == 'CHARGING' and soc_ratio >= 0.90:
        self.state = 'HOLDING'
        self.state_age = 0
      elif self.state == 'CHARGING' and price_percentile > self.charge_threshold + 15:
        self.state = 'HOLDING'
        self.state_age = 0
      elif self.state == 'DISCHARGING' and soc_ratio <= 0.20:
        self.state = 'HOLDING'
        self.state_age = 0
      elif self.state == 'DISCHARGING' and price_percentile < self.discharge_threshold - 15:
        self.state = 'HOLDING'
        self.state_age = 0
      elif self.state == 'HOLDING' and price_percentile < self.charge_threshold and soc_ratio < 0.85:
        self.state = 'CHARGING'
        self.state_age = 0
      elif self.state == 'HOLDING' and price_percentile > self.discharge_threshold and soc_ratio > 0.25:
        self.state = 'DISCHARGING'
        self.state_age = 0

    # Action generation based on state
    action_kw = 0.0

    if self.state == 'CHARGING':
      # Charge hard when in low-price regime
      if soc_ratio < self.aggressive_charge_target:
        action_kw = max_charge_rate  # Full power
      elif soc_ratio < 0.90:
        action_kw = max_charge_rate * 0.6
      else:
        action_kw = 0.0

    elif self.state == 'DISCHARGING':
      # Discharge hard when in high-price regime
      if soc_ratio > self.aggressive_discharge_target:
        action_kw = -max_discharge_rate  # Full power
      elif soc_ratio > 0.25:
        action_kw = -max_discharge_rate * 0.7
      else:
        action_kw = 0.0

    else:  # HOLDING
      # Gentle opportunistic actions during hold state
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 2.0 and soc_ratio < 0.80 and price_percentile < 60:
        action_kw = min(pv_excess * 0.8, max_charge_rate * 0.3)
      elif current_demand_kw > current_pv_generation_kw + 2.0 and soc_ratio > 0.30 and price_percentile > 40:
        action_kw = -min(current_demand_kw - current_pv_generation_kw, max_discharge_rate * 0.2)

    return action_kw
