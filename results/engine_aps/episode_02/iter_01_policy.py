class DispatchPolicy:
  def __init__(self):
    """Initializes the policy with state tracking across time steps."""
    self.hour_to_prices = {}
    self.hour_count = {}

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
    """Decide this hour's flexible dispatch with aggressive battery charging strategy."""

    if hour_of_day not in self.hour_to_prices:
      self.hour_to_prices[hour_of_day] = []
      self.hour_count[hour_of_day] = 0

    self.hour_to_prices[hour_of_day].append(current_price)
    self.hour_count[hour_of_day] += 1

    battery_space = battery_capacity_mwh - battery_soc_mwh

    hour_avg = (sum(self.hour_to_prices[hour_of_day]) / len(self.hour_to_prices[hour_of_day])) \
      if hour_of_day in self.hour_to_prices and self.hour_to_prices[hour_of_day] else current_price

    battery_mw = 0.0
    if current_price < hour_avg * 0.95 and battery_space > 10:
      charge_amount = min(battery_power_mw, battery_space / 0.8)
      battery_mw = charge_amount
    elif current_price > hour_avg * 1.4 and battery_soc_mwh > 10:
      discharge_amount = min(battery_power_mw, battery_soc_mwh)
      battery_mw = -discharge_amount

    if oldest_backlog_age_h > 20:
      flex_serve_mw = arriving_flex_mw + min(backlog_mwh / 1.0, arriving_flex_mw)
    elif current_price < hour_avg * 0.9:
      flex_serve_mw = arriving_flex_mw + min(backlog_mwh / 4.0, arriving_flex_mw)
    elif current_price > hour_avg * 1.3:
      flex_serve_mw = max(0, arriving_flex_mw * 0.2)
    else:
      flex_serve_mw = arriving_flex_mw

    return flex_serve_mw, battery_mw
