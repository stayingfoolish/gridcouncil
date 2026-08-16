class Policy:
  def __init__(self):
    """Two-layer hierarchical strategy with price forecasting."""
    self.price_history = []
    self.max_history = 12
    self.charge_rate_kw = 2.0
    self.discharge_rate_kw = 2.0

    self.soc_min = 0.10
    self.soc_max = 0.90

    self.price_forecast_window = 4
    self.arbitrage_threshold = 0.20
    self.momentum_threshold = 0.10

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Hierarchical decision: arbitrage first, then renewable smoothing."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    action_kw = 0.0

    if len(self.price_history) >= 4:
      recent_avg = (self.price_history[-1] + self.price_history[-2]) / 2
      earlier_avg = (self.price_history[-3] + self.price_history[-4]) / 2
      price_trend = (recent_avg - earlier_avg) / (earlier_avg + 0.001)

      spread_ratio = current_grid_sell_price / (current_grid_buy_price + 0.001)

      if price_trend < -self.momentum_threshold and spread_ratio > 1.05:
        if current_soc < 0.75:
          charge_available = min(
            self.charge_rate_kw,
            battery_capacity_kwh * 0.75 - current_energy_stored_kwh
          )
          if charge_available > 0.1:
            return charge_available

      if price_trend > self.momentum_threshold and spread_ratio > 1.05:
        if current_soc > 0.35:
          discharge_available = min(
            self.discharge_rate_kw,
            current_energy_stored_kwh - battery_capacity_kwh * 0.35
          )
          if discharge_available > 0.1:
            return -discharge_available

      if spread_ratio > (1.0 + self.arbitrage_threshold):
        if current_soc > self.soc_min:
          discharge_available = min(
            self.discharge_rate_kw * 0.8,
            current_energy_stored_kwh - battery_capacity_kwh * self.soc_min
          )
          if discharge_available > 0.1:
            return -discharge_available

    if current_demand_kw > current_pv_generation_kw:
      energy_deficit = current_demand_kw - current_pv_generation_kw
      if current_soc > self.soc_min + 0.05:
        discharge_for_demand = min(
          self.discharge_rate_kw * 0.3,
          energy_deficit,
          current_energy_stored_kwh - battery_capacity_kwh * (self.soc_min + 0.05)
        )
        if discharge_for_demand > 0.05:
          return -discharge_for_demand

    if current_pv_generation_kw > current_demand_kw:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if current_soc < self.soc_max - 0.05:
        charge_from_pv = min(
          self.charge_rate_kw * 0.4,
          pv_excess,
          battery_capacity_kwh * (self.soc_max - 0.05) - current_energy_stored_kwh
        )
        if charge_from_pv > 0.05:
          return charge_from_pv

    return 0.0
