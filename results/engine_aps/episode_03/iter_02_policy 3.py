class DispatchPolicy:
  def __init__(self):
    """Market-adaptive dispatch with urgency scaling and forward-looking battery planning."""
    self.price_history = []
    self.backlog_history = []
    self.battery_cycle_plan = {}

    # Percentile thresholds (adaptive to market)
    self.low_percentile = 30
    self.high_percentile = 70

    # Backlog urgency curve parameters
    self.deadline_buffer_hours = 2.0
    self.critical_age_threshold = 20.0

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
    """Market-adaptive dispatch with urgency scaling and cycle planning."""

    # Update rolling history
    self.price_history.append(current_price)
    self.backlog_history.append(backlog_mwh)
    if len(self.price_history) > 72:
      self.price_history.pop(0)
    if len(self.backlog_history) > 72:
      self.backlog_history.pop(0)

    # Compute dynamic thresholds from market history
    if len(self.price_history) >= 24:
      low_threshold = sorted(self.price_history)[len(self.price_history) * self.low_percentile // 100]
      high_threshold = sorted(self.price_history)[len(self.price_history) * self.high_percentile // 100]
    else:
      low_threshold = 35.0
      high_threshold = 90.0

    # Compute continuous backlog urgency (0.0 to 1.0)
    # Urgency increases smoothly as backlog ages, peaks at deadline - buffer
    hours_to_deadline = 24.0 - oldest_backlog_age_h
    if hours_to_deadline <= self.deadline_buffer_hours:
      backlog_urgency = 1.0  # Critical: must serve
    elif oldest_backlog_age_h >= self.critical_age_threshold:
      # Sigmoid ramp-up from critical age to deadline
      urgency_factor = (oldest_backlog_age_h - self.critical_age_threshold) / (24.0 - self.critical_age_threshold)
      backlog_urgency = 0.3 + 0.7 * min(1.0, urgency_factor)
    else:
      backlog_urgency = 0.1  # Low urgency for fresh arrivals

    # Flex serve: blend price-based and urgency-based decisions
    price_signal = 0.0
    if current_price <= low_threshold:
      price_signal = 0.2  # Prefer charging battery
    elif current_price >= high_threshold:
      price_signal = 0.8  # Prefer deferring compute
    else:
      price_signal = 0.4 + 0.4 * (current_price - low_threshold) / max(1.0, high_threshold - low_threshold)

    # Blend: high urgency overrides price signal; low urgency respects market timing
    serve_fraction = 0.3 + 0.6 * (backlog_urgency + (1.0 - price_signal)) / 2.0
    flex_serve_mw = arriving_flex_mw * serve_fraction + (backlog_mwh / 2.0) * backlog_urgency / 24.0
    flex_serve_mw = max(0.0, min(flex_serve_mw, arriving_flex_mw + backlog_mwh / 1.0))

    # Battery strategy: look-ahead cycle planning
    soc_ratio = battery_soc_mwh / max(1.0, battery_capacity_mwh)

    battery_mw = 0.0

    # Charge if: price is below 40th percentile AND battery not full AND prices expected to rise
    avg_future_price = sum(self.price_history[-24:]) / max(1, len(self.price_history[-24:])) if len(self.price_history) >= 24 else 60.0
    if current_price < low_threshold * 0.9 and soc_ratio < 0.90:
      battery_mw = min(battery_power_mw * 1.2, battery_capacity_mwh - battery_soc_mwh)

    # Discharge if: (1) high price OR (2) backlog urgency high AND battery has charge
    if (current_price > high_threshold * 1.1) or (backlog_urgency > 0.6 and soc_ratio > 0.20):
      discharge_amount = battery_power_mw * (0.5 + backlog_urgency)
      battery_mw = -min(discharge_amount, battery_soc_mwh)

    return flex_serve_mw, battery_mw
