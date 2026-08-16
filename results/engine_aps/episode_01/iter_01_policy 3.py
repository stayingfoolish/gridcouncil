class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Internal state (price history, thresholds,
    counters) can be kept here across time steps."""
    self.price_history = []
    self.avg_price = 60.0

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

    self.price_history.append(current_price)
    if len(self.price_history) > 24:
      self.price_history.pop(0)

    if len(self.price_history) > 0:
      self.avg_price = sum(self.price_history) / len(self.price_history)

    momentum = 0
    if len(self.price_history) >= 7:
      momentum = self.price_history[-1] - self.price_history[-7]

    flex_serve_mw = arriving_flex_mw
    battery_mw = 0.0

    if oldest_backlog_age_h >= 23:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh, 250)
      return flex_serve_mw, battery_mw

    if current_price <= self.avg_price and battery_soc_mwh < battery_capacity_mwh * 0.8:
      if momentum < -10:
        charge_threshold = 1.0
      elif momentum < -5:
        charge_threshold = 0.9
      else:
        charge_threshold = 0.75

      if current_price <= self.avg_price * charge_threshold:
        available_charge_capacity = battery_capacity_mwh - battery_soc_mwh
        charge_amount = min(available_charge_capacity / 1.0, battery_power_mw)
        battery_mw = charge_amount

    if current_price >= self.avg_price * 1.15:
      if backlog_mwh > 0:
        flex_serve_mw = min(arriving_flex_mw + backlog_mwh, 250)

      available_discharge = min(battery_soc_mwh, battery_power_mw)

      if momentum > 5:
        battery_mw = -available_discharge * 0.8
      elif momentum > 0:
        battery_mw = -available_discharge * 0.6
      else:
        battery_mw = -available_discharge * 0.5
    elif current_price > self.avg_price * 1.2:
      flex_serve_mw = max(0, arriving_flex_mw - backlog_mwh)

    return flex_serve_mw, battery_mw
