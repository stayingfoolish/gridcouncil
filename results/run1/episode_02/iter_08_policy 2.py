class Policy:
  def __init__(self):
    self.price_history = []
    self.demand_history = []
    self.action_history = []
    self.realized_spreads = []
    self.missed_opportunities = []
    self.max_history = 72

    self.charge_rate_kw = 2.0
    self.discharge_rate_kw = 2.0
    self.battery_efficiency = 0.92

    self.charge_threshold = 0.85
    self.discharge_threshold = 1.25
    self.reserve_soc = 0.40
    self.regret_amplifier = 1.0

    self.price_forecast_1h = 0.0
    self.price_forecast_6h = 0.0
    self.price_forecast_24h = 0.0
    self.forecast_confidence = 0.3

    self.recent_volatility = 0.0
    self.regime_persistence = 0.0

  def compute_price_percentiles(self, price_history, window_hours=24):
    if len(price_history) < 12:
      return 0.5, 0.5, 0.5

    recent_window = price_history[-window_hours:] if len(price_history) >= window_hours else price_history
    sorted_prices = sorted(recent_window)
    median = sorted_prices[len(sorted_prices)//2]
    q25 = sorted_prices[len(sorted_prices)//4]
    q75 = sorted_prices[3*len(sorted_prices)//4]

    current_price = price_history[-1]

    pct = sum(1 for p in sorted_prices if p <= current_price) / len(sorted_prices)

    return pct, (current_price / (median + 0.001)), (q75 - q25) / (median + 0.001)

  def forecast_price_trajectories(self, price_history, demand_history):
    if len(price_history) < 24:
      return 0.0, 0.0, 0.0, 0.2

    recent_prices = price_history[-24:]
    recent_mean = sum(recent_prices) / len(recent_prices)

    long_term_mean = sum(price_history) / len(price_history) if len(price_history) > 24 else recent_mean
    mean_reversion_strength = 0.15

    momentum = (price_history[-1] - price_history[-6]) / (price_history[-6] + 0.001)
    momentum_decay = 0.7

    if len(demand_history) >= 24:
      demand_recent = sum(demand_history[-6:]) / 6
      demand_mean = sum(demand_history) / len(demand_history)
      demand_ratio = (demand_recent / (demand_mean + 0.001)) - 1.0
    else:
      demand_ratio = 0.0

    forecast_1h = recent_mean * (1 - mean_reversion_strength) + long_term_mean * mean_reversion_strength
    forecast_1h += recent_mean * momentum * momentum_decay
    forecast_1h *= (1 + demand_ratio * 0.2)

    forecast_6h = recent_mean * (1 - mean_reversion_strength * 0.5) + long_term_mean * mean_reversion_strength * 0.5
    forecast_6h *= (1 + demand_ratio * 0.1)

    forecast_24h = long_term_mean

    confidence = max(0.3, 0.8 - abs(momentum) * 0.2)

    return forecast_1h, forecast_6h, forecast_24h, confidence

  def compute_opportunity_value(self, current_price, current_soc, battery_capacity,
                                forecast_1h, forecast_6h, forecast_24h, confidence):
    value_charge = 0.0
    if forecast_1h < current_price * 0.98:
      value_charge += (current_price - forecast_1h) * (1 - current_soc) * confidence
    if forecast_6h < current_price * 0.95:
      value_charge += (current_price - forecast_6h) * 0.3 * (1 - current_soc) * confidence

    value_discharge = 0.0
    if forecast_1h > current_price * 1.02:
      value_discharge += (forecast_1h - current_price) * current_soc * confidence
    if forecast_6h > current_price * 1.05:
      value_discharge += (forecast_6h - current_price) * 0.3 * current_soc * confidence

    return value_charge, value_discharge

  def update_adaptive_thresholds(self, recent_volatility, regret_count, performance_history):
    volatility_factor = 1.0 + recent_volatility * 3.0

    regret_factor = 1.0 + regret_count * 0.1 if regret_count > 0 else 1.0

    recent_perf = sum(performance_history[-3:]) / 3 if len(performance_history) >= 3 else 0
    recent_best = min(performance_history[-5:]) if len(performance_history) >= 5 else recent_perf
    perf_factor = 1.2 if recent_perf > recent_best * 1.5 else 1.0

    self.charge_threshold = 0.75 * volatility_factor * perf_factor
    self.discharge_threshold = 1.35 * (1 / volatility_factor) / perf_factor

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    action_kw = 0.0

    if len(self.price_history) >= 24:
      percentile, price_ratio, spread_width = self.compute_price_percentiles(self.price_history)

      f1h, f6h, f24h, confidence = self.forecast_price_trajectories(self.price_history, self.demand_history)

      opp_charge, opp_discharge = self.compute_opportunity_value(
        current_grid_buy_price, current_soc, battery_capacity_kwh,
        f1h, f6h, f24h, confidence
      )

      recent_vol = self._compute_recent_volatility()
      self.update_adaptive_thresholds(recent_vol, len(self.missed_opportunities),
                                      self.realized_spreads[-5:] if self.realized_spreads else [0])

      price_vs_median = price_ratio

      charge_signal = (percentile < 0.35) and (f6h > current_grid_buy_price * 1.02 or current_soc < self.reserve_soc)
      if charge_signal and current_soc < 0.95:
        charge_available = min(
          self.charge_rate_kw,
          (battery_capacity_kwh * 0.95 - current_energy_stored_kwh) / self.battery_efficiency
        )
        if charge_available > 0.1:
          action_kw = charge_available * (0.7 + 0.3 * confidence)

      discharge_signal = (percentile > 0.65) and (f6h < current_grid_buy_price * 0.98 or current_soc > 0.75)
      if discharge_signal and current_soc > 0.20:
        discharge_available = min(
          self.discharge_rate_kw,
          current_energy_stored_kwh - battery_capacity_kwh * 0.05
        )
        if discharge_available > 0.1:
          action_kw = -discharge_available * (0.7 + 0.3 * confidence)

      if action_kw > 0:
        self.realized_spreads.append(current_grid_buy_price)
      elif action_kw < 0:
        self.realized_spreads.append(current_grid_sell_price)

    if action_kw == 0.0:
      if current_demand_kw > current_pv_generation_kw and current_soc > 0.15:
        action_kw = -min(0.3, current_soc * battery_capacity_kwh * 0.1)
      elif current_pv_generation_kw > current_demand_kw and current_soc < 0.85:
        action_kw = min(0.3, (1 - current_soc) * battery_capacity_kwh * 0.1)

    return action_kw

  def _compute_recent_volatility(self):
    if len(self.price_history) < 6:
      return 0.0
    recent = self.price_history[-6:]
    mean = sum(recent) / len(recent)
    variance = sum((p - mean)**2 for p in recent) / len(recent)
    return variance ** 0.5 / (mean + 0.001)
