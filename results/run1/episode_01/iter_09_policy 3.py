class Policy:
  def __init__(self):
    """Momentum-driven opportunistic battery trading"""
    self.price_history = []
    self.momentum_window = 10
    self.max_history = 40

    self.momentum_threshold = 0.15
    self.strong_charge_momentum = -0.25
    self.strong_discharge_momentum = 0.25

    self.soc_target_charge = 0.95
    self.soc_target_discharge = 0.05
    self.soc_neutral = 0.40

    self.max_power = 10.0
    self.aggressive_power = 10.0
    self.conservative_power = 3.0

  def calculate_momentum(self, current_price):
    """Compute price velocity (change per period)"""
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    if len(self.price_history) < self.momentum_window:
      return 0.0

    recent_prices = self.price_history[-self.momentum_window:]
    momentum = (recent_prices[-1] - recent_prices[0]) / (self.momentum_window - 1)
    return momentum

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    momentum = self.calculate_momentum(current_grid_buy_price)

    if momentum < self.strong_charge_momentum:
      if current_soc < self.soc_target_charge:
        max_charge = battery_capacity_kwh - current_energy_stored_kwh
        return min(self.aggressive_power, max_charge)
      else:
        return 0.0

    elif momentum > self.strong_discharge_momentum:
      if current_soc > self.soc_target_discharge:
        max_discharge = current_energy_stored_kwh
        return -min(self.aggressive_power, max_discharge)
      else:
        return 0.0

    else:
      net_power = current_pv_generation_kw - current_demand_kw

      if abs(net_power) > 2.0:
        if net_power > 0 and current_soc < 0.90:
          max_charge = battery_capacity_kwh - current_energy_stored_kwh
          return min(net_power * 0.8, max_charge)
        elif net_power < 0 and current_soc > 0.10:
          max_discharge = current_energy_stored_kwh
          return max(net_power * 0.8, -max_discharge)

      soc_delta = self.soc_neutral - current_soc
      if abs(soc_delta) > 0.10:
        proportional = soc_delta * self.conservative_power
        if proportional > 0:
          max_charge = battery_capacity_kwh - current_energy_stored_kwh
          return min(proportional, max_charge)
        else:
          max_discharge = current_energy_stored_kwh
          return max(proportional, -max_discharge)

      return 0.0
