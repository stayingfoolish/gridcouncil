class DispatchPolicy:
  def __init__(self):
    self.committed_windows = []
    self.price_history = {}
    self.hourly_volatility = [0.0] * 24
    self.strategic_plan = {}
    self.episodes = 0

  def _compute_volatility(self, hour_of_day: int):
    if hour_of_day not in self.price_history or len(self.price_history[hour_of_day]) < 3:
      return 1.0
    prices = self.price_history[hour_of_day]
    mean = sum(prices) / len(prices)
    if mean < 1:
      return 1.0
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return (variance ** 0.5) / mean

  def _identify_dispatch_windows(self, hour_of_day: int, backlog_mwh: float,
                                  oldest_age_h: float, current_price: float) -> list:
    windows = []
    urgency_factor = 1.0 + (oldest_age_h / 24.0)

    for start_offset in range(1, 49):
      start_h = (hour_of_day + start_offset) % 24
      window_cost = current_price * 12

      if window_cost < current_price * 5:
        windows.append({
          'start': start_h,
          'duration': 12,
          'cost': window_cost / 12,
          'mwh_target': backlog_mwh,
          'deadline_h': hour_of_day + 24 * urgency_factor
        })

    return sorted(windows, key=lambda w: w['cost'])[:3]

  def _dynamic_arbitrage_threshold(self, hour_of_day: int, backlog_mwh: float,
                                    soc_ratio: float, hourly_volatility: list) -> float:
    base_threshold = 25.0
    vol_mult = 1.0 + hourly_volatility[hour_of_day]
    backlog_mult = 1.0 - min(backlog_mwh / 200, 0.5)
    battery_mult = 0.8 if soc_ratio > 0.70 else (1.2 if soc_ratio < 0.30 else 1.0)

    return base_threshold * vol_mult * backlog_mult * battery_mult

  def _contingency_reserve(self, battery_soc_mwh: float, battery_capacity_mwh: float,
                           oldest_backlog_age_h: float, backlog_mwh: float) -> float:
    if oldest_backlog_age_h < 12:
      return 0.0
    reserve_pct = min(0.5, 0.1 + (oldest_backlog_age_h - 12) / 12)
    return battery_soc_mwh * reserve_pct

  def take_action(self, hour_of_day: int, current_price: float, firm_load_mw: float,
                  arriving_flex_mw: float, backlog_mwh: float, oldest_backlog_age_h: float,
                  battery_soc_mwh: float, battery_capacity_mwh: float,
                  battery_power_mw: float) -> tuple:

    if hour_of_day not in self.price_history:
      self.price_history[hour_of_day] = []
    self.price_history[hour_of_day].append(current_price)
    if len(self.price_history[hour_of_day]) > 20:
      self.price_history[hour_of_day].pop(0)

    self.hourly_volatility[hour_of_day] = self._compute_volatility(hour_of_day)

    if hour_of_day % 6 == 0 and backlog_mwh > 50:
      self.committed_windows = self._identify_dispatch_windows(
        hour_of_day, backlog_mwh, oldest_backlog_age_h, current_price
      )

    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0
    threshold = self._dynamic_arbitrage_threshold(hour_of_day, backlog_mwh,
                                                   soc_ratio, self.hourly_volatility)
    reserve = self._contingency_reserve(battery_soc_mwh, battery_capacity_mwh,
                                        oldest_backlog_age_h, backlog_mwh)

    flex_serve_mw = arriving_flex_mw
    battery_mw = 0.0

    if oldest_backlog_age_h >= 20:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh * 0.85, 250)
      available = min(battery_soc_mwh - reserve, battery_power_mw)
      battery_mw = -available * 0.75 if available > 10 else 0.0
      return flex_serve_mw, battery_mw

    in_window = any(w['start'] == hour_of_day for w in self.committed_windows)

    if in_window and backlog_mwh > 30:
      window = next(w for w in self.committed_windows if w['start'] == hour_of_day)
      mwh_to_clear = min(backlog_mwh * 0.6, window['mwh_target'])
      flex_serve_mw = min(arriving_flex_mw + mwh_to_clear, 250)
    elif threshold > 0 and soc_ratio > 0.45 and backlog_mwh > 0:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh * 0.5, 250)
    else:
      flex_serve_mw = arriving_flex_mw

    available_charge = battery_capacity_mwh - battery_soc_mwh
    available_discharge = min(battery_soc_mwh - reserve, battery_power_mw)

    if current_price < current_price * 0.9 and available_charge > 20:
      battery_mw = min(available_charge, battery_power_mw * 0.8)
    elif threshold > 0 and available_discharge > 20:
      battery_mw = -available_discharge * 0.7
    elif oldest_backlog_age_h > 16 and available_discharge > 10:
      battery_mw = -available_discharge * 0.4

    return flex_serve_mw, battery_mw
