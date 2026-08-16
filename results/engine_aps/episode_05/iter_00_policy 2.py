class DispatchPolicy:
  def __init__(self):
    self.price_history = []
    self.hour_counter = 0
    self.price_threshold_multiplier = 1.2

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

    avg_price = sum(self.price_history) / len(self.price_history)
    price_threshold = avg_price * self.price_threshold_multiplier

    flex_serve_mw = 0.0
    battery_mw = 0.0

    is_cheap_hour = current_price < price_threshold
    is_expensive_hour = current_price > price_threshold

    hours_until_deadline = 24 - oldest_backlog_age_h if backlog_mwh > 0 else float('inf')
    urgent = hours_until_deadline <= 2
    backlog_critical = hours_until_deadline <= 1

    if backlog_critical or (urgent and backlog_mwh > 0):
      flex_serve_mw = min(arriving_flex_mw + (backlog_mwh / max(hours_until_deadline, 1)), 250)
    elif is_cheap_hour:
      flex_serve_mw = arriving_flex_mw + min(backlog_mwh, 100)
      flex_serve_mw = min(flex_serve_mw, 250)
    elif is_expensive_hour:
      flex_serve_mw = 0.0
    else:
      flex_serve_mw = arriving_flex_mw

    available_charge_capacity = battery_capacity_mwh - battery_soc_mwh
    available_discharge_capacity = battery_soc_mwh

    if is_cheap_hour and available_charge_capacity > 0:
      battery_mw = min(battery_power_mw, available_charge_capacity / 1.0)
    elif is_expensive_hour and available_discharge_capacity > 0:
      if backlog_critical or urgent:
        battery_mw = -min(battery_power_mw, available_discharge_capacity)
      else:
        battery_mw = -min(battery_power_mw * 0.5, available_discharge_capacity)
    elif backlog_critical and available_discharge_capacity > 0:
      battery_mw = -min(battery_power_mw, available_discharge_capacity)
    else:
      battery_mw = 0.0

    if backlog_mwh > 0 and flex_serve_mw > arriving_flex_mw:
      excess_discharge = (flex_serve_mw - arriving_flex_mw) * 1.0 / 0.88
      if battery_mw > 0:
        battery_mw = 0.0
      else:
        battery_mw = -min(battery_power_mw, excess_discharge)

    self.hour_counter += 1

    return flex_serve_mw, battery_mw
