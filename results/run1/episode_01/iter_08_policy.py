class Policy:
  def __init__(self):
    """Predictive state-based battery cycling"""
    self.price_history = []
    self.price_z_scores = []
    self.soc_target = 0.5
    self.max_history = 60  # Shorter window for faster adaptation

    # Predictive price zone parameters
    self.price_mean_window = 20
    self.price_std_window = 20
    self.z_score_threshold = 0.8  # Trigger on 0.8σ deviation

    # Aggressive SOC targeting
    self.charge_target_low_price = 0.95   # Near-full when price is cheap
    self.discharge_target_high_price = 0.10 # Near-empty when price is expensive
    self.neutral_soc = 0.50

    # Power commitment
    self.max_power_rate = 10.0  # kW (unscaled—commit fully)
    self.base_charge_power = 8.0
    self.base_discharge_power = 8.0

  def calculate_price_z_score(self, current_price):
    """Normalize current price relative to recent history"""
    if len(self.price_history) < self.price_mean_window:
      return 0.0

    recent = self.price_history[-self.price_mean_window:]
    mean_price = sum(recent) / len(recent)

    # Variance calculation
    variance = sum((p - mean_price) ** 2 for p in recent) / len(recent)
    std_dev = variance ** 0.5

    if std_dev < 0.01:  # Avoid division by near-zero
      return 0.0

    z_score = (current_price - mean_price) / std_dev
    return z_score

  def compute_soc_target(self, buy_price, sell_price, soc_z_score):
    """Set SOC target based on price position relative to demand"""
    spread = sell_price - buy_price

    # If buy price is unusually low (>1σ below mean): charge aggressively
    if soc_z_score < -0.8:
      return self.charge_target_low_price

    # If sell price is unusually high (>0.8σ above mean): discharge aggressively
    if soc_z_score > 0.8:
      return self.discharge_target_high_price

    # Spread-aware neutral: if spread is wide, stay charged; if narrow, discharge
    if spread > 0.20:
      return 0.70  # Prepare to sell soon
    elif spread < 0.05:
      return 0.30  # Prepare to buy soon
    else:
      return self.neutral_soc

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute predictive SOC targeting"""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Compute z-score for current buy price
    buy_z_score = self.calculate_price_z_score(current_grid_buy_price)

    # Determine SOC target
    target_soc = self.compute_soc_target(current_grid_buy_price, current_grid_sell_price, buy_z_score)
    self.soc_target = target_soc

    # Calculate delta from target
    soc_delta = target_soc - current_soc

    # Determine action magnitude
    if abs(soc_delta) > 0.15:
      # Large gap: commit full power
      if soc_delta > 0:
        # Charge to reach target
        max_charge_kwh = battery_capacity_kwh - current_energy_stored_kwh
        action_kw = min(self.base_charge_power, max_charge_kwh)
      else:
        # Discharge to reach target
        max_discharge_kwh = current_energy_stored_kwh
        action_kw = -min(self.base_discharge_power, max_discharge_kwh)

    elif abs(soc_delta) > 0.05:
      # Medium gap: proportional power
      proportional_power = abs(soc_delta) * self.base_charge_power * 1.5
      if soc_delta > 0:
        max_charge_kwh = battery_capacity_kwh - current_energy_stored_kwh
        action_kw = min(proportional_power, max_charge_kwh)
      else:
        max_discharge_kwh = current_energy_stored_kwh
        action_kw = -min(proportional_power, max_discharge_kwh)

    else:
      # Close to target: minimal adjustment + renewable matching
      net_power = current_pv_generation_kw - current_demand_kw
      if abs(net_power) < 0.5:
        action_kw = 0.0
      else:
        action_kw = net_power * 0.5  # Weak reactive mode only when balanced

    return action_kw
