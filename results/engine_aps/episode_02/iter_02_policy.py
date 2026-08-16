class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Internal state (price history, thresholds,
    counters) can be kept here across time steps."""
    self.hourly_prices = []
    self.hourly_price_avgs = [None] * 24

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
    # Track price history for each hour
    self.hourly_prices.append((hour_of_day, current_price))

    # Calculate hourly price average if we have enough data
    hour_prices = [p for h, p in self.hourly_prices if h == hour_of_day]
    if hour_prices:
      hour_avg = sum(hour_prices) / len(hour_prices)
      self.hourly_price_avgs[hour_of_day] = hour_avg
    else:
      hour_avg = current_price

    # Determine flex load to serve
    flex_serve_mw = 0.0
    battery_mw = 0.0

    # Urgency threshold: serve backlog if oldest is near 24-hour deadline
    urgency_hours = 3

    if oldest_backlog_age_h >= 24 - urgency_hours:
      # Must serve backlog urgently
      flex_serve_mw = min(backlog_mwh, arriving_flex_mw + backlog_mwh)
    elif current_price < hour_avg * 0.85:
      # Very cheap hour: serve aggressively including backlog
      flex_serve_mw = arriving_flex_mw + min(backlog_mwh * 0.5, 100)
    elif current_price < hour_avg * 0.95:
      # Cheap hour: serve all arriving load
      flex_serve_mw = arriving_flex_mw
    else:
      # Normal or expensive hour: defer flex load
      flex_serve_mw = arriving_flex_mw * 0.3

    # Battery dispatch logic with improved thresholds
    battery_space = battery_capacity_mwh - battery_soc_mwh

    # Improved discharge: lower threshold to 1.25x and minimum SoC to 5 MWh
    if current_price > hour_avg * 1.25 and battery_soc_mwh > 5:
      discharge_amount = min(battery_power_mw, battery_soc_mwh)
      battery_mw = -discharge_amount
    # Improved charge: lower minimum to 5 MWh
    elif current_price < hour_avg * 0.95 and battery_space > 5:
      charge_amount = min(battery_power_mw, battery_space)
      battery_mw = charge_amount
    else:
      battery_mw = 0.0

    return flex_serve_mw, battery_mw
