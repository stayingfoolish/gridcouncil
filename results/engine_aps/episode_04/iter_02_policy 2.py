class DispatchPolicy:
  def __init__(self):
    """Momentum-driven optimizer with dynamic thresholds and spread awareness."""
    self.price_history = []
    self.max_history = 72

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
    """Decision logic using momentum, spread, and deadline risk."""
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    recent_prices = self.price_history[-12:] if len(self.price_history) >= 12 else self.price_history
    short_momentum = (recent_prices[-1] - recent_prices[0]) / len(recent_prices) if recent_prices else 0

    avg_recent = sum(recent_prices) / len(recent_prices) if recent_prices else current_price
    volatility = (sum((p - avg_recent) ** 2 for p in recent_prices) / len(recent_prices)) ** 0.5 if recent_prices else 0

    long_avg = sum(self.price_history) / len(self.price_history) if self.price_history else current_price
    price_percentile = current_price / long_avg if long_avg > 0 else 1.0

    deadline_urgency = 0.0
    if oldest_backlog_age_h >= 15:
      deadline_urgency = min((oldest_backlog_age_h - 15) / 10.0, 1.0)

    momentum_factor = -short_momentum / max(volatility, 1.0) if volatility > 0 else 0
    serve_threshold_multiplier = 1.0 + momentum_factor * 0.2 - deadline_urgency * 0.3

    flex_serve_threshold = long_avg * max(0.8, min(1.2, serve_threshold_multiplier))

    if current_price <= flex_serve_threshold:
      serve_fraction = 0.7 + deadline_urgency * 0.3
      backlog_to_serve = backlog_mwh * serve_fraction
      flex_serve_mw = arriving_flex_mw + backlog_to_serve
    elif deadline_urgency > 0.5:
      backlog_to_serve = backlog_mwh * (0.3 + deadline_urgency * 0.4)
      flex_serve_mw = arriving_flex_mw + backlog_to_serve
    else:
      flex_serve_mw = arriving_flex_mw

    soc_ratio = battery_soc_mwh / battery_capacity_mwh if battery_capacity_mwh > 0 else 0.5

    charge_threshold = long_avg * 0.70 - volatility * 0.5
    discharge_threshold = long_avg * 1.30 + volatility * 0.5

    battery_mw = 0.0

    if current_price <= charge_threshold and soc_ratio < 0.80:
      available_charge = min(battery_capacity_mwh - battery_soc_mwh, battery_power_mw)
      battery_mw = available_charge
    elif current_price >= discharge_threshold and soc_ratio > 0.20:
      available_discharge = min(battery_soc_mwh, battery_power_mw)
      battery_mw = -available_discharge
    else:
      battery_mw = 0.0

    return flex_serve_mw, battery_mw
