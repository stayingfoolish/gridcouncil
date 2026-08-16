class DispatchPolicy:
  def __init__(self):
    self.price_history = []
    self.hour_to_prices = {}
    self.max_history = 168

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
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    if hour_of_day not in self.hour_to_prices:
      self.hour_to_prices[hour_of_day] = []
    self.hour_to_prices[hour_of_day].append(current_price)

    avg_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_price
    min_price = min(self.price_history) if self.price_history else current_price
    price_percentile_30 = min_price + 0.3 * (avg_price - min_price) if avg_price > min_price else current_price

    flex_serve_mw = 0.0
    urgency_ratio = oldest_backlog_age_h / 24.0 if backlog_mwh > 0 else 0.0

    if urgency_ratio > 0.75:
      flex_serve_mw = min(arriving_flex_mw + backlog_mwh / max(oldest_backlog_age_h, 1.0), 250.0)
    elif current_price < price_percentile_30:
      flex_serve_mw = arriving_flex_mw
    elif current_price < avg_price and arriving_flex_mw > 0:
      flex_serve_mw = arriving_flex_mw * 0.6
    elif urgency_ratio > 0.3 and backlog_mwh > 0:
      flex_serve_mw = min(arriving_flex_mw + min(50.0, backlog_mwh / max(oldest_backlog_age_h, 1.0)), 250.0)

    if flex_serve_mw < 250.0 and backlog_mwh > 0:
      backlog_available = backlog_mwh / max(oldest_backlog_age_h, 1.0)
      backlog_serve = min(backlog_available, 250.0 - flex_serve_mw)
      if current_price < avg_price * 1.1 or urgency_ratio > 0.5:
        flex_serve_mw += backlog_serve

    battery_mw = 0.0
    battery_space = battery_capacity_mwh - battery_soc_mwh

    if current_price < price_percentile_30 and battery_space > 10:
      charge_amount = min(battery_power_mw, battery_space / 1.5)
      battery_mw = charge_amount
    elif current_price > avg_price * 1.4 and battery_soc_mwh > battery_capacity_mwh * 0.15:
      discharge_amount = min(battery_power_mw, battery_soc_mwh * 0.7)
      battery_mw = -discharge_amount
    elif current_price > avg_price and battery_soc_mwh > battery_capacity_mwh * 0.25:
      battery_mw = -min(battery_power_mw * 0.4, battery_soc_mwh * 0.5)

    flex_serve_mw = max(0.0, min(flex_serve_mw, 250.0))
    return flex_serve_mw, battery_mw
