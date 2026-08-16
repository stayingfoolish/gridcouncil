class DispatchPolicy:
  def __init__(self):
    self.price_window = []
    self.hourly_p25 = [0.0] * 24
    self.hourly_p75 = [0.0] * 24
    self.hourly_count = [0] * 24
    self.backlog_history = []
    self.arbitrage_threshold = 25.0
    self.episodes = 0

  def _update_percentile_bands(self, hour_of_day: int, price: float):
    alpha = 0.05
    current_count = self.hourly_count[hour_of_day]

    if current_count == 0:
      self.hourly_p25[hour_of_day] = price
      self.hourly_p75[hour_of_day] = price
    else:
      self.hourly_p25[hour_of_day] = (
        alpha * min(price, self.hourly_p25[hour_of_day]) +
        (1 - alpha) * self.hourly_p25[hour_of_day]
      )
      self.hourly_p75[hour_of_day] = (
        alpha * max(price, self.hourly_p75[hour_of_day]) +
        (1 - alpha) * self.hourly_p75[hour_of_day]
      )
    self.hourly_count[hour_of_day] += 1

  def _look_ahead_max_price(self, hour_of_day: int, lookahead_hours: int = 12) -> float:
    max_forecast = 0.0
    for i in range(1, lookahead_hours + 1):
      future_hour = (hour_of_day + i) % 24
      max_forecast = max(max_forecast, self.hourly_p75[future_hour])
    return max_forecast

  def _compute_backlog_cost(self, age_h: float, mwh: float, current_price: float) -> float:
    hourly_delay_cost = mwh * current_price

    if age_h > 16:
      deadline_penalty = mwh * current_price * (2.0 ** ((age_h - 16) / 4))
    else:
      deadline_penalty = 0.0

    return hourly_delay_cost + deadline_penalty

  def _get_battery_strategy(self, soc_ratio: float, hour_of_day: int,
                            current_price: float, max_future_price: float) -> str:
    spread = max_future_price - current_price

    if soc_ratio > 0.85:
      return "discharge"

    if soc_ratio < 0.15:
      return "charge"

    if spread > self.arbitrage_threshold and soc_ratio > 0.40:
      return "hold_for_discharge"

    if current_price < self.hourly_p25[hour_of_day] and soc_ratio < 0.60:
      return "charge"

    return "hold"

  def take_action(self,
    hour_of_day: int,
    current_price: float,
    firm_load_mw: float,
    arriving_flex_mw: float,
    backlog_mwh: float,
    oldest_backlog_age_h: float,
    battery_soc_mwh: float,
    battery_capacity_mwh: float,
    battery_power_mw: float,
  ) -> tuple:

    self.price_window.append(current_price)
    if len(self.price_window) > 336:
      self.price_window.pop(0)

    self._update_percentile_bands(hour_of_day, current_price)

    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0
    max_future_price = self._look_ahead_max_price(hour_of_day, lookahead_hours=12)
    battery_strategy = self._get_battery_strategy(soc_ratio, hour_of_day, current_price, max_future_price)

    flex_serve_mw = arriving_flex_mw
    battery_mw = 0.0

    if oldest_backlog_age_h >= 23.0:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh, 250)
      return flex_serve_mw, battery_mw

    if oldest_backlog_age_h >= 20.0:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh * 0.7, 250)
      if battery_strategy == "discharge" or soc_ratio > 0.50:
        available = min(battery_soc_mwh, battery_power_mw)
        battery_mw = -available * 0.60
      return flex_serve_mw, battery_mw

    spread = max_future_price - current_price
    backlog_cost = self._compute_backlog_cost(oldest_backlog_age_h, backlog_mwh, current_price)

    if backlog_mwh > 0 and spread > self.arbitrage_threshold and oldest_backlog_age_h < 16:
      flex_serve_mw = arriving_flex_mw
    elif backlog_mwh > 0 and current_price < self.hourly_p25[hour_of_day] * 1.05:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh * 0.9, 250)
    else:
      flex_serve_mw = arriving_flex_mw

    available_charge = battery_capacity_mwh - battery_soc_mwh
    available_discharge = min(battery_soc_mwh, battery_power_mw)

    if battery_strategy == "charge":
      battery_mw = min(available_charge, battery_power_mw * 0.9)
    elif battery_strategy == "discharge":
      battery_mw = -available_discharge * 0.85
    elif battery_strategy == "hold_for_discharge" and current_price < max_future_price - 30:
      battery_mw = 0.0

    return flex_serve_mw, battery_mw
