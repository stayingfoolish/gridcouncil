class Policy:
  def __init__(self):
    """Buy-sell spread arbitrage with state machine."""
    self.max_charge_rate = 12.0
    self.max_discharge_rate = 10.0

    # Price history for recent baseline (short window)
    self.recent_prices = []
    self.recent_window = 15  # Shorter, more responsive

    # State machine
    self.state = "NEUTRAL"  # NEUTRAL, CHARGING, DISCHARGING
    self.state_entry_time = 0

    # Spread-based thresholds
    self.charge_spread_threshold = 0.05  # Buy if spread > 5%
    self.discharge_spread_threshold = 0.08  # Sell if spread > 8%

    # SOC extremes for capture
    self.min_soc = 0.1   # More extreme
    self.max_soc = 0.9   # More extreme
    self.aggressive_charge_target = 0.85
    self.aggressive_discharge_target = 0.15

    # State persistence (avoid flip-flopping)
    self.min_state_duration = 2
    self.state_change_penalty = 0.05

  def calculate_spread_metrics(self, buy_price, sell_price):
    """Calculate spread and spread percentile."""
    if buy_price < 1e-6:
      return 0.0, 0.0

    spread = (buy_price - sell_price) / buy_price
    return max(0.0, spread)

  def update_price_baseline(self, price):
    """Track recent price baseline for relative positioning."""
    self.recent_prices.append(price)
    if len(self.recent_prices) > self.recent_window:
      self.recent_prices.pop(0)

  def get_price_percentile(self, current_price):
    """Percentile within recent prices (not all history)."""
    if len(self.recent_prices) < 3:
      return 0.5
    sorted_p = sorted(self.recent_prices)
    return sum(1 for p in sorted_p if p <= current_price) / len(sorted_p)

  def evaluate_charge_action(self, current_soc, spread, price_percentile, available_to_charge):
    """Charge when spread is high AND price is low relative to recent."""
    if available_to_charge < 0.1:
      return 0.0

    # Spread must exceed threshold
    if spread < self.charge_spread_threshold:
      return 0.0

    # Price should be in lower half of recent range (good entry)
    if price_percentile > 0.6:
      return 0.0

    # Aggressive: charge toward 85% to maximize arbitrage capture
    energy_gap = (self.aggressive_charge_target - current_soc) * 100
    rate = self.max_charge_rate * (1.0 + energy_gap / 100)
    return min(rate, available_to_charge)

  def evaluate_discharge_action(self, current_soc, spread, price_percentile, available_to_discharge):
    """Discharge when spread is high AND price is high relative to recent."""
    if available_to_discharge < 0.1:
      return 0.0

    # Spread must exceed (higher) threshold
    if spread < self.discharge_spread_threshold:
      return 0.0

    # Price should be in upper half of recent range (good exit)
    if price_percentile < 0.4:
      return 0.0

    # Aggressive: discharge toward 15% to maximize arbitrage capture
    energy_gap = (current_soc - self.aggressive_discharge_target) * 100
    rate = self.max_discharge_rate * (1.0 + energy_gap / 100)
    return min(rate, available_to_discharge)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """State machine + spread arbitrage."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Update baselines
    self.update_price_baseline(current_grid_buy_price)

    # Calculate metrics
    spread = self.calculate_spread_metrics(current_grid_buy_price, current_grid_sell_price)
    price_pct = self.get_price_percentile(current_grid_buy_price)

    # Energy constraints
    min_energy = battery_capacity_kwh * self.min_soc
    max_energy = battery_capacity_kwh * self.max_soc
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)
    available_to_charge = max(0, max_energy - current_energy_stored_kwh)

    # Evaluate actions
    charge_rate = self.evaluate_charge_action(current_soc, spread, price_pct, available_to_charge)
    discharge_rate = self.evaluate_discharge_action(current_soc, spread, price_pct, available_to_discharge)

    # State machine logic
    if charge_rate > 0.5 and (self.state != "DISCHARGING" or self.state_entry_time > self.min_state_duration):
      self.state = "CHARGING"
      self.state_entry_time = 0
      return charge_rate

    elif discharge_rate > 0.5 and (self.state != "CHARGING" or self.state_entry_time > self.min_state_duration):
      self.state = "DISCHARGING"
      self.state_entry_time = 0
      return -discharge_rate

    else:
      # Neutral: continue current action or hold
      self.state = "NEUTRAL"
      self.state_entry_time += 1
      return 0.0
