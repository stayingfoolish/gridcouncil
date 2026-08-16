class Policy:
  def __init__(self):
    self.price_history = []
    self.extended_history_len = 48
    self.charge_percentile_threshold = 0.30
    self.discharge_percentile_threshold = 0.70

    self.min_soc_target = 0.10
    self.max_soc_target = 0.90

    self.max_charge_power = 12.0
    self.max_discharge_power = 11.0
    self.margin_power_multiplier = 1.4

    self.accumulated_margin = 0.0
    self.current_cycle_buy_price = None
    self.active_buy_cycle = False

  def calculate_price_percentile(self, price_history, current_price):
    if len(price_history) < 5:
      return 0.5

    prices_sorted = sorted(price_history)
    rank = sum(1 for p in prices_sorted if p < current_price) / len(prices_sorted)
    return rank

  def calculate_margin_potential(self, price_history, current_price):
    if len(price_history) < 10:
      return 0.0

    future_prices = price_history[-10:]
    avg_future = sum(future_prices) / len(future_prices)
    potential_margin = avg_future - current_price
    return potential_margin

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.extended_history_len:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    price_percentile = self.calculate_price_percentile(self.price_history, current_grid_buy_price)
    margin_potential = self.calculate_margin_potential(self.price_history, current_grid_buy_price)

    action_kw = 0.0

    if price_percentile < self.charge_percentile_threshold:
      available = battery_capacity_kwh - current_energy_stored_kwh
      if available > 0.1:
        charge_power = self.max_charge_power * self.margin_power_multiplier
        intensity_boost = 1.0 + (0.3 - price_percentile) * 2.0
        action_kw = min(charge_power * intensity_boost, available)
        self.active_buy_cycle = True
        self.current_cycle_buy_price = current_grid_buy_price

    elif price_percentile > self.discharge_percentile_threshold:
      available = current_energy_stored_kwh
      if available > 0.1:
        discharge_power = self.max_discharge_power * self.margin_power_multiplier
        intensity_boost = 1.0 + (price_percentile - 0.70) * 2.5
        action_kw = -min(discharge_power * intensity_boost, available)

        if self.active_buy_cycle and self.current_cycle_buy_price:
          cycle_margin = current_grid_sell_price - self.current_cycle_buy_price
          self.accumulated_margin += cycle_margin * action_kw / self.max_discharge_power
          self.active_buy_cycle = False

    else:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 0.3 and current_soc < self.max_soc_target:
        action_kw = min(pv_excess * 0.8, self.max_charge_power * 0.4,
                       battery_capacity_kwh - current_energy_stored_kwh)
      elif pv_excess < -0.3 and current_soc > self.min_soc_target:
        action_kw = -min(abs(pv_excess) * 0.6, self.max_discharge_power * 0.4,
                        current_energy_stored_kwh)

    new_soc = current_energy_stored_kwh + action_kw
    if new_soc > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_soc < 0:
      action_kw = -current_energy_stored_kwh

    return action_kw
