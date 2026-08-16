class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Maintains price history and thresholds."""
    self.price_history = []
    self.low_threshold = 40.0
    self.mid_price = 65.0
    self.high_threshold = 85.0

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
    """Decide this hour's flexible dispatch with aggressive battery utilization."""

    # Update rolling 24-hour price history
    self.price_history.append(current_price)
    if len(self.price_history) > 24:
      self.price_history.pop(0)

    # Flexible serve logic based on price and backlog age
    if oldest_backlog_age_h >= 23.0:
      # Force serve old backlog to avoid deadline violations
      flex_serve_mw = arriving_flex_mw + min(backlog_mwh / 1.0, battery_power_mw * 2)
    elif current_price <= self.low_threshold:
      # Low price: serve all arriving flex to allow battery charging
      flex_serve_mw = arriving_flex_mw
    elif current_price <= self.mid_price:
      # Medium price: serve partial flex, preserve battery for later discharge
      flex_serve_mw = arriving_flex_mw * 0.6
    else:
      # High price: defer flex, rely on battery discharge
      flex_serve_mw = arriving_flex_mw * 0.3

    flex_serve_mw = max(0.0, min(flex_serve_mw, arriving_flex_mw + backlog_mwh / 1.0))

    # Aggressive battery logic with expanded discharge window
    soc_ratio = battery_soc_mwh / max(1.0, battery_capacity_mwh)

    battery_mw = 0.0
    if current_price <= self.low_threshold and soc_ratio < 0.95:
      # Charge to near-full capacity during low-price hours
      battery_mw = min(battery_power_mw, battery_capacity_mwh - battery_soc_mwh)
    elif current_price >= self.mid_price and soc_ratio > 0.15:
      # Discharge at medium prices to reduce peak load and deadline violations
      battery_mw = -min(battery_power_mw, battery_soc_mwh)

    return flex_serve_mw, battery_mw
