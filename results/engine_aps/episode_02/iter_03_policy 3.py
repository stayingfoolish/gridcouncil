class DispatchPolicy:
  def __init__(self):
    """Predictive energy arbitrage optimizer with forecast-based dispatch."""
    self.hourly_prices = []
    self.hourly_price_forecast = [None] * 24
    self.forecast_confidence = 0.5
    self.target_soc_mwh = 50.0
    self.compute_buffer = []
    self.price_history_24h = []

  def _update_price_forecast(self, hour_of_day: int, current_price: float):
    """Build rolling 24-hour price forecast."""
    self.hourly_prices.append((hour_of_day, current_price))
    self.price_history_24h.append(current_price)
    if len(self.price_history_24h) > 24:
      self.price_history_24h.pop(0)

    hour_prices = [p for h, p in self.hourly_prices if h == hour_of_day]
    if len(hour_prices) >= 2:
      hour_avg = sum(hour_prices) / len(hour_prices)
      hour_std = (sum((p - hour_avg) ** 2 for p in hour_prices) / len(hour_prices)) ** 0.5
      self.hourly_price_forecast[hour_of_day] = {
        'mean': hour_avg,
        'std': hour_std,
        'trend': hour_avg
      }

  def _identify_cheap_window(self) -> tuple:
    """Find next cheapest 4-hour window for compute execution."""
    prices = self.hourly_price_forecast
    min_price = float('inf')
    min_window_start = 0

    for i in range(24):
      window_price = sum([
        (prices[((i + j) % 24)]['mean'] if prices[((i + j) % 24)] else 100)
        for j in range(4)
      ]) / 4
      if window_price < min_price:
        min_price = window_price
        min_window_start = i

    return min_window_start, min_price

  def _identify_expensive_hours(self) -> list:
    """Find hours with prices > 90th percentile for discharge."""
    prices = self.hourly_price_forecast
    all_prices = [
      (prices[h]['mean'] if prices[h] else 100) for h in range(24)
    ]
    threshold = sorted(all_prices)[int(len(all_prices) * 0.9)]
    return [h for h in range(24) if (prices[h] and prices[h]['mean'] > threshold)]

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
    """Predictive dispatch: shift compute to cheap hours, battery to expensive hours."""

    self._update_price_forecast(hour_of_day, current_price)

    battery_mw = 0.0

    if len(self.hourly_price_forecast) >= 12:
      cheap_window_start, cheap_avg = self._identify_cheap_window()
      expensive_hours = self._identify_expensive_hours()

      is_current_cheap = (hour_of_day >= cheap_window_start and
                          hour_of_day < cheap_window_start + 4)
      is_current_expensive = hour_of_day in expensive_hours

      battery_space = battery_capacity_mwh - battery_soc_mwh
      min_soc = 10.0

      if is_current_expensive and battery_soc_mwh > min_soc:
        discharge_amount = min(battery_power_mw, battery_soc_mwh - min_soc)
        battery_mw = -discharge_amount
      elif is_current_cheap and battery_soc_mwh < self.target_soc_mwh:
        charge_amount = min(battery_power_mw, battery_space)
        battery_mw = charge_amount
      elif current_price < (self.hourly_price_forecast[hour_of_day]['mean'] * 0.90
                            if self.hourly_price_forecast[hour_of_day] else current_price):
        charge_amount = min(battery_power_mw * 0.5, battery_space)
        battery_mw = charge_amount

    flex_serve_mw = 0.0

    if oldest_backlog_age_h >= 23:
      flex_serve_mw = min(backlog_mwh + arriving_flex_mw, arriving_flex_mw + backlog_mwh)
    elif len(self.hourly_price_forecast) >= 12:
      cheap_window_start, _ = self._identify_cheap_window()
      if hour_of_day >= cheap_window_start and hour_of_day < cheap_window_start + 4:
        flex_serve_mw = arriving_flex_mw + min(backlog_mwh * 0.7, 150)
      elif oldest_backlog_age_h >= 18:
        flex_serve_mw = arriving_flex_mw + min(backlog_mwh * 0.3, 75)
      else:
        flex_serve_mw = arriving_flex_mw * 0.15
    else:
      flex_serve_mw = arriving_flex_mw * 0.2

    return flex_serve_mw, battery_mw
