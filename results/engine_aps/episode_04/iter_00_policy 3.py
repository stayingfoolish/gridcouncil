class DispatchPolicy:
  def __init__(self):
    self.price_history = []

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

    self.price_history.append(current_price)
    if len(self.price_history) > 24:
      self.price_history.pop(0)

    avg_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_price

    flex_serve_mw = 0.0

    if oldest_backlog_age_h >= 23:
      flex_serve_mw = arriving_flex_mw + min(100, backlog_mwh)
    elif current_price <= avg_price:
      flex_serve_mw = arriving_flex_mw

    battery_mw = 0.0
    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0

    if current_price < avg_price * 0.85 and soc_ratio < 0.75:
      space = battery_capacity_mwh - battery_soc_mwh
      battery_mw = min(battery_power_mw, space)
    elif current_price > avg_price * 1.15 and soc_ratio > 0.15:
      battery_mw = -min(battery_power_mw, battery_soc_mwh)

    return flex_serve_mw, battery_mw
