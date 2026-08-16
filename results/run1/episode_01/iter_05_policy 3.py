class Policy:
  def __init__(self):
    """Momentum-driven adaptive battery management"""
    self.price_history = []
    self.max_history = 30
    self.short_window = 3
    self.medium_window = 10

  def calculate_momentum(self):
    """Calculate price momentum score from trend strength"""
    if len(self.price_history) < self.medium_window:
      return 0.0, 0.0

    short_prices = self.price_history[-self.short_window:]
    short_slope = (short_prices[-1] - short_prices[0]) / (self.short_window - 1)
    short_avg = sum(short_prices) / len(short_prices)
    short_momentum = short_slope / (short_avg + 0.01)

    medium_prices = self.price_history[-self.medium_window:]
    medium_slope = (medium_prices[-1] - medium_prices[0]) / (self.medium_window - 1)
    medium_avg = sum(medium_prices) / len(medium_prices)
    medium_momentum = medium_slope / (medium_avg + 0.01)

    short_momentum_norm = max(-1.0, min(1.0, short_momentum * 10))
    medium_momentum_norm = max(-1.0, min(1.0, medium_momentum * 10))

    combined = 0.4 * short_momentum_norm + 0.6 * medium_momentum_norm

    agreement = 1.0 - abs(short_momentum_norm - medium_momentum_norm) / 2.0

    return combined, agreement

  def calculate_volatility(self):
    """Standard volatility measurement"""
    if len(self.price_history) < 10:
      return 0.0
    recent = self.price_history[-10:]
    mean_price = sum(recent) / len(recent)
    variance = sum((p - mean_price) ** 2 for p in recent) / len(recent)
    return (variance ** 0.5) / (mean_price + 0.01)

  def calculate_target_soc(self, momentum, confidence, volatility):
    """Determine optimal SoC based on momentum with safety overrides"""

    if volatility > 0.20:
      confidence_threshold = 0.6
    else:
      confidence_threshold = 0.5

    if confidence < confidence_threshold:
      return 0.50

    if momentum < -0.5:
      return 0.85
    elif momentum < -0.2:
      return 0.70
    elif momentum < 0.2:
      return 0.50
    elif momentum < 0.5:
      return 0.30
    else:
      return 0.15

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute momentum-driven control strategy"""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    momentum, confidence = self.calculate_momentum()
    volatility = self.calculate_volatility()
    target_soc = self.calculate_target_soc(momentum, confidence, volatility)

    if volatility < 0.15:
      soc_band = 0.04
    elif volatility < 0.25:
      soc_band = 0.06
    else:
      soc_band = 0.10

    soc_gap = target_soc - current_soc
    action_kw = 0.0

    if soc_gap > soc_band:
      error_magnitude = min(soc_gap / 0.3, 1.0)
      max_charge_rate = battery_capacity_kwh * 0.40
      charge_power = min(14.0 * error_magnitude, max_charge_rate)
      action_kw = charge_power
    elif soc_gap < -soc_band:
      error_magnitude = min(-soc_gap / 0.3, 1.0)
      max_discharge_rate = battery_capacity_kwh * 0.40
      discharge_power = min(14.0 * error_magnitude, max_discharge_rate)
      action_kw = -discharge_power
    else:
      net_power = current_pv_generation_kw - current_demand_kw
      smoothing_factor = 0.50 + 0.35 * abs(momentum)
      if net_power > 0.3:
        action_kw = min(net_power * smoothing_factor, 12.0)
      elif net_power < -0.3:
        action_kw = max(net_power * smoothing_factor, -12.0)

    max_charge = battery_capacity_kwh - current_energy_stored_kwh
    max_discharge = current_energy_stored_kwh

    if action_kw > 0:
      action_kw = min(action_kw, max_charge)
    else:
      action_kw = max(action_kw, -max_discharge)

    return action_kw
