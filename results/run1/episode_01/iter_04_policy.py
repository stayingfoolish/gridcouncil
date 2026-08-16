class Policy:
  def __init__(self):
    """Adaptive SoC target trading for batteries"""
    self.price_history = []
    self.action_history = []
    self.max_history = 20
    self.volatility_window = 10
    self.price_percentile_threshold = 0.4

  def calculate_volatility_and_percentile(self, current_price):
    """Measure price volatility and current price rank"""
    if len(self.price_history) < self.volatility_window:
      return 0.0, 0.5

    recent = self.price_history[-self.volatility_window:]
    mean_price = sum(recent) / len(recent)
    variance = sum((p - mean_price) ** 2 for p in recent) / len(recent)
    volatility = (variance ** 0.5) / (mean_price + 0.01)

    lower_prices = sum(1 for p in recent if p <= current_price)
    percentile = lower_prices / len(recent)

    return volatility, percentile

  def calculate_target_soc(self, current_price, volatility, percentile,
                           current_soc, net_power):
    """Determine optimal SoC based on price conditions and volatility"""

    base_target = 0.50

    if volatility > 0.15:
      if percentile < self.price_percentile_threshold:
        base_target = 0.75
      else:
        base_target = 0.25
    elif percentile < self.price_percentile_threshold:
      base_target = 0.65
    else:
      base_target = 0.35

    if net_power > 2.0:
      base_target = min(base_target + 0.10, 0.90)
    elif net_power < -2.0:
      base_target = max(base_target - 0.10, 0.10)

    return base_target

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute actions to track dynamic SoC target"""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    net_power = current_pv_generation_kw - current_demand_kw
    volatility, percentile = self.calculate_volatility_and_percentile(current_grid_buy_price)
    target_soc = self.calculate_target_soc(current_grid_buy_price, volatility,
                                          percentile, current_soc, net_power)

    action_kw = 0.0

    soc_gap = target_soc - current_soc

    if soc_gap > 0.08:
      max_charge_rate = battery_capacity_kwh * 0.40
      charge_power = min(14.0, max_charge_rate, battery_capacity_kwh - current_energy_stored_kwh)
      action_kw = charge_power

    elif soc_gap < -0.08:
      max_discharge_rate = battery_capacity_kwh * 0.40
      discharge_power = min(14.0, max_discharge_rate, current_energy_stored_kwh * 0.5)
      action_kw = -discharge_power

    else:
      if net_power > 0.5 and current_soc < target_soc + 0.05:
        action_kw = min(net_power * 0.85, 12.0)
      elif net_power < -0.5 and current_soc > target_soc - 0.05:
        action_kw = max(net_power * 0.85, -12.0)

    max_charge = battery_capacity_kwh - current_energy_stored_kwh
    max_discharge = current_energy_stored_kwh

    if action_kw > 0:
      action_kw = min(action_kw, max_charge)
    else:
      action_kw = max(action_kw, -max_discharge)

    return action_kw
