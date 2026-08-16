class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Tracks price history and thresholds across time steps."""
    self.price_history = []
    self.max_history = 48
    self.charge_threshold = 60.0
    self.discharge_threshold = 85.0

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
      flex_serve_mw: deferrable compute to run now [MW]
      battery_mw: positive = charge, negative = discharge [MW]
    """
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    avg_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_price

    deadline_multiplier = 1.0
    if oldest_backlog_age_h >= 20:
      deadline_multiplier = 1.0 + (oldest_backlog_age_h - 20) ** 1.5

    flex_serve_threshold = avg_price * (1.15 - 0.15 * min(deadline_multiplier / 4.0, 1.0))

    flex_serve_mw = 0.0

    if current_price <= flex_serve_threshold:
      backlog_to_serve = min(deadline_multiplier * 50, backlog_mwh)
      flex_serve_mw = arriving_flex_mw + backlog_to_serve
    elif current_price > flex_serve_threshold and oldest_backlog_age_h >= 20:
      backlog_to_serve = min(deadline_multiplier * 30, backlog_mwh)
      flex_serve_mw = arriving_flex_mw + backlog_to_serve
    else:
      flex_serve_mw = arriving_flex_mw

    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0.5

    battery_mw = 0.0
    if current_price <= self.charge_threshold and soc_ratio < 0.85:
      available_charge_capacity = min(
        battery_capacity_mwh - battery_soc_mwh,
        battery_power_mw
      )
      battery_mw = min(available_charge_capacity, battery_power_mw)
    elif current_price >= self.discharge_threshold and soc_ratio > 0.15:
      available_discharge_capacity = min(battery_soc_mwh, battery_power_mw)
      battery_mw = -min(available_discharge_capacity, battery_power_mw)

    return flex_serve_mw, battery_mw
