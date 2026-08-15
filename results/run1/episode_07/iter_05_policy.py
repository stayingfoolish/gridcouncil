class Policy:
  def __init__(self):
    """Percentile-based energy arbitrage with explicit operating modes."""
    self.max_charge_rate = 12.0
    self.max_discharge_rate = 10.0

    self.price_history = []
    self.price_window = 20

    self.sell_percentile = 0.75
    self.charge_percentile = 0.25
    self.neutral_zone = (0.35, 0.65)

    self.min_soc = 0.20
    self.max_soc = 0.80
    self.target_soc_neutral = 0.50

    self.mode = "IDLE"
    self.mode_history = []

    self.mode_change_threshold = 2
    self.signal_count = 0
    self.last_signal = None

  def calculate_price_percentile(self, current_price):
    """Determine where current price sits in historical distribution."""
    self.price_history.append(current_price)
    if len(self.price_history) > self.price_window:
      self.price_history.pop(0)

    if len(self.price_history) < 5:
      return 0.5

    sorted_prices = sorted(self.price_history)
    percentile = len([p for p in sorted_prices if p < current_price]) / len(sorted_prices)
    return percentile

  def determine_mode(self, current_soc, price_percentile, surplus_pv, deficit_demand):
    """Explicit mode selection with anti-oscillation logic."""
    if price_percentile > self.sell_percentile and current_soc > self.min_soc:
      desired_signal = "DISCHARGE"
    elif price_percentile < self.charge_percentile and current_soc < self.max_soc:
      desired_signal = "CHARGE"
    elif surplus_pv > 0.5 and current_soc < self.max_soc:
      desired_signal = "PV_UTILIZE"
    elif deficit_demand > 0.5 and current_soc > self.min_soc + 0.1:
      desired_signal = "SUPPLY_DEMAND"
    else:
      desired_signal = "IDLE"

    if desired_signal == self.last_signal:
      self.signal_count += 1
      if self.signal_count >= self.mode_change_threshold:
        self.mode = desired_signal
        self.signal_count = 0
    else:
      self.last_signal = desired_signal
      self.signal_count = 1

    return self.mode

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Mode-based decision making with percentile arbitrage."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    price_percentile = self.calculate_price_percentile(current_grid_buy_price)

    min_energy = battery_capacity_kwh * self.min_soc
    max_energy = battery_capacity_kwh * self.max_soc
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)
    available_to_charge = max(0, max_energy - current_energy_stored_kwh)

    surplus_pv = max(0, current_pv_generation_kw - current_demand_kw)
    deficit_demand = max(0, current_demand_kw - current_pv_generation_kw)

    mode = self.determine_mode(current_soc, price_percentile, surplus_pv, deficit_demand)

    if mode == "DISCHARGE" and available_to_discharge > 0:
      return -min(self.max_discharge_rate, available_to_discharge)

    elif mode == "CHARGE" and available_to_charge > 0:
      return min(self.max_charge_rate, available_to_charge)

    elif mode == "PV_UTILIZE" and available_to_charge > 0:
      return min(self.max_charge_rate, surplus_pv, available_to_charge)

    elif mode == "SUPPLY_DEMAND" and available_to_discharge > 0:
      return -min(self.max_discharge_rate, available_to_discharge, deficit_demand)

    else:
      return 0.0
