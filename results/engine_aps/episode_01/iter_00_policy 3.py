class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Internal state (price history, thresholds,
    counters) can be kept here across time steps."""
    self.price_history = []
    self.hour_count = 0
    self.daily_low = float('inf')
    self.daily_high = 0

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
    """Decide this hour's flexible dispatch.

    Returns:
      (flex_serve_mw, battery_mw)
      flex_serve_mw: deferrable compute to run now [MW] (0 = defer everything;
        serving more than arriving_flex_mw works down the backlog)
      battery_mw: positive = charge, negative = discharge [MW]
    """
    self.hour_count += 1
    self.price_history.append(current_price)
    if len(self.price_history) > 24:
      self.price_history.pop(0)

    if hour_of_day == 0:
      self.daily_low = float('inf')
      self.daily_high = 0

    self.daily_low = min(self.daily_low, current_price)
    self.daily_high = max(self.daily_high, current_price)

    avg_price = sum(self.price_history) / len(self.price_history)
    price_percentile = (current_price - self.daily_low) / max(1, self.daily_high - self.daily_low) if self.daily_high > self.daily_low else 0.5

    backlog_deadline_hours = 24 - oldest_backlog_age_h

    flex_serve_mw = 0.0
    battery_mw = 0.0

    if backlog_deadline_hours <= 1:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh / max(1, backlog_deadline_hours), 250)
    elif current_price <= avg_price * 0.8:
      flex_serve_mw = arriving_flex_mw
      available_charge_capacity = battery_capacity_mwh - battery_soc_mwh
      charge_amount = min(available_charge_capacity / 1.0, battery_power_mw)
      battery_mw = charge_amount
    elif current_price >= avg_price * 1.2:
      if backlog_mwh > 0:
        flex_serve_mw = min(arriving_flex_mw + backlog_mwh, 250)
      available_discharge = min(battery_soc_mwh, battery_power_mw)
      if backlog_mwh > arriving_flex_mw:
        battery_mw = -available_discharge
    else:
      if backlog_mwh > 0 and backlog_deadline_hours <= 6:
        flex_serve_mw = min(arriving_flex_mw + backlog_mwh / max(1, backlog_deadline_hours + 1), 250)
      else:
        flex_serve_mw = arriving_flex_mw * 0.5

      if current_price < avg_price and battery_soc_mwh < battery_capacity_mwh * 0.7:
        available_charge = min(battery_capacity_mwh - battery_soc_mwh, battery_power_mw)
        battery_mw = available_charge * 0.5

    flex_serve_mw = max(0, min(flex_serve_mw, 250))
    battery_mw = max(-battery_power_mw, min(battery_mw, battery_power_mw))

    return flex_serve_mw, battery_mw
