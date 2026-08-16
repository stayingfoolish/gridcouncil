class Policy:
  def __init__(self):
    """Time-aware cycle arbitrage with active-zone SOC management"""
    self.price_history = []
    self.max_history = 48
    self.time_step = 0
    self.recent_spreads = []
    self.cycle_count = 0
    self.target_soc = 0.50

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Maximize arbitrage cycles through active-zone SOC management and spread exploitation"""
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    self.recent_spreads.append(current_grid_sell_price - current_grid_buy_price)
    if len(self.recent_spreads) > 12:
      self.recent_spreads.pop(0)

    self.time_step += 1
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh

    time_bucket = (self.time_step % 144) // 24

    current_spread = current_grid_sell_price - current_grid_buy_price
    avg_spread = sum(self.recent_spreads) / len(self.recent_spreads) if self.recent_spreads else 0.01

    high_spread_period = current_spread > avg_spread * 1.3

    action_kw = 0.0

    if high_spread_period:
      if soc_ratio > 0.60:
        action_kw = -max_discharge_rate * 0.9
        self.cycle_count += 1
        return action_kw
      elif soc_ratio < 0.40:
        action_kw = max_charge_rate * 0.9
        return action_kw

    if soc_ratio > 0.65:
      action_kw = -max_discharge_rate * 0.6
    elif soc_ratio < 0.35:
      action_kw = max_charge_rate * 0.6

    elif 0.40 < soc_ratio < 0.60:
      buy_signal = current_grid_buy_price < (sum(self.price_history) / len(self.price_history)) * 0.97
      sell_signal = current_grid_sell_price > (sum(self.price_history) / len(self.price_history)) * 1.03

      if sell_signal:
        action_kw = -max_discharge_rate * 0.5
      elif buy_signal:
        action_kw = max_charge_rate * 0.5

    if action_kw == 0.0:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 1.0 and soc_ratio < 0.55:
        action_kw = min(pv_excess * 0.7, max_charge_rate * 0.4)
      elif current_demand_kw > current_pv_generation_kw + 1.0 and soc_ratio > 0.45:
        action_kw = -min(current_demand_kw - current_pv_generation_kw, max_discharge_rate * 0.3)

    return action_kw
