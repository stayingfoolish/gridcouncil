class Policy:
  def __init__(self):
    """Demand-responsive renewable harvesting with opportunistic arbitrage"""
    self.price_history = []
    self.demand_history = []
    self.pv_history = []
    self.max_history = 24
    self.trend_window = 6

    self.pv_harvest_threshold = 0.3
    self.demand_support_threshold = 0.2

    self.cheap_price_percentile = 30
    self.expensive_price_percentile = 70

  def _calculate_price_percentile(self, current_price):
    """Calculate percentile, return 50 if insufficient data"""
    if len(self.price_history) < 4:
      return 50
    sorted_prices = sorted(self.price_history)
    rank = sum(1 for p in sorted_prices if p < current_price) / len(sorted_prices) * 100
    return rank

  def _get_price_momentum(self):
    """Recent price trend: positive=rising, negative=falling"""
    if len(self.price_history) < self.trend_window:
      return 0
    recent = self.price_history[-self.trend_window:]
    return (recent[-1] - recent[0]) / self.trend_window

  def _get_demand_forecast(self):
    """Estimate if demand is trending up"""
    if len(self.demand_history) < self.trend_window:
      return 0
    recent = self.demand_history[-self.trend_window:]
    return (recent[-1] - recent[0]) / self.trend_window

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Opportunistic renewable harvesting with demand-matching"""
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    self.pv_history.append(current_pv_generation_kw)

    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
    if len(self.demand_history) > self.max_history:
      self.demand_history.pop(0)
    if len(self.pv_history) > self.max_history:
      self.pv_history.pop(0)

    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    pv_excess = current_pv_generation_kw - current_demand_kw
    demand_deficit = current_demand_kw - current_pv_generation_kw

    price_percentile = self._calculate_price_percentile(current_grid_buy_price)
    price_momentum = self._get_price_momentum()
    demand_trend = self._get_demand_forecast()

    action_kw = 0.0

    if pv_excess > self.pv_harvest_threshold and soc_ratio < 0.95:
      action_kw = min(pv_excess * 1.0, max_charge_rate)

    elif demand_deficit > self.demand_support_threshold and soc_ratio > 0.15:
      if price_percentile > 60 or demand_trend > 0:
        action_kw = -min(demand_deficit * 0.9, max_discharge_rate)
      else:
        action_kw = -min(demand_deficit * 0.7, max_discharge_rate)

    elif price_percentile < self.cheap_price_percentile and soc_ratio < 0.85:
      if price_momentum < 0:
        action_kw = max_charge_rate
      else:
        action_kw = max_charge_rate * 0.85

    elif price_percentile > self.expensive_price_percentile and soc_ratio > 0.30:
      if price_momentum > 0:
        action_kw = -max_discharge_rate
      else:
        action_kw = -max_discharge_rate * 0.75

    elif demand_trend > 0.05 and soc_ratio < 0.75 and price_percentile < 50:
      action_kw = max_charge_rate * 0.5

    return action_kw
