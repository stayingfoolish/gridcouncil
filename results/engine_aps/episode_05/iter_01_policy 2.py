class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Internal state (price history, thresholds,
    counters) can be kept here across time steps."""
    self.price_history = []
    self.max_history_length = 168  # One week of hourly prices
    self.avg_price = 50.0

  def calculate_dynamic_threshold_multiplier(self, price_history):
    if len(price_history) < 2:
        return 1.2

    avg_price = sum(price_history) / len(price_history)
    variance = sum((p - avg_price) ** 2 for p in price_history) / len(price_history)
    std_dev = variance ** 0.5

    # Volatility as coefficient of variation
    volatility = std_dev / avg_price if avg_price > 0 else 0

    # Scale multiplier inversely with volatility:
    # High volatility (>0.4) → multiplier 1.0 (conservative)
    # Medium volatility (0.2-0.4) → multiplier 1.2 (baseline)
    # Low volatility (<0.2) → multiplier 1.4 (aggressive)
    if volatility > 0.4:
        return 1.0
    elif volatility > 0.2:
        return 1.0 + (1.2 - 1.0) * (0.4 - volatility) / 0.2
    else:
        return min(1.0 + (1.4 - 1.0) * (0.2 - volatility) / 0.2, 1.4)

  def take_action(self,
    # hour of day 0-23
    hour_of_day: int,
    # baseline day-ahead price this hour [$/MWh]
    current_price: float,
    # inflexible data-center load this hour [MW]
    firm_load_mw: float,
    # newly arriving deferrable compute this hour [MW]
    arriving_flex_mw: float,
    # deferred compute waiting to be served [MWh]
    backlog_mwh: float,
    # age of the oldest deferred compute [hours] (deadline: 24)
    oldest_backlog_age_h: float,
    # battery state of charge [MWh]
    battery_soc_mwh: float,
    # battery capacity [MWh]
    battery_capacity_mwh: float,
    # battery power limit [MW]
    battery_power_mw: float,
  ) -> tuple:
    """Decide this hour's flexible dispatch.

    Returns:
      (flex_serve_mw, battery_mw)
      flex_serve_mw: deferrable compute to run now [MW] (0 = defer everything;
        serving more than arriving_flex_mw works down the backlog)
      battery_mw: positive = charge, negative = discharge [MW]
    """
    # Update price history
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history_length:
        self.price_history.pop(0)

    # Calculate average price for threshold
    if len(self.price_history) > 0:
        self.avg_price = sum(self.price_history) / len(self.price_history)

    # Calculate dynamic threshold multiplier based on volatility
    dynamic_multiplier = self.calculate_dynamic_threshold_multiplier(self.price_history)
    price_threshold = self.avg_price * dynamic_multiplier

    # Determine flex serve amount
    flex_serve_mw = 0.0
    battery_mw = 0.0

    # Urgency factor based on backlog age
    urgency_factor = 1.0
    if oldest_backlog_age_h > 20:
        urgency_factor = 2.0
    elif oldest_backlog_age_h > 16:
        urgency_factor = 1.5

    adjusted_threshold = price_threshold / urgency_factor

    # Decision logic
    if current_price <= adjusted_threshold:
        # Cheap hour: serve flex compute and charge battery if possible
        flex_serve_mw = arriving_flex_mw

        # Calculate available capacity for battery charging
        battery_available_capacity = battery_capacity_mwh - battery_soc_mwh
        charge_rate = min(battery_power_mw, battery_available_capacity)

        if charge_rate > 0 and current_price < self.avg_price * 0.9:
            battery_mw = charge_rate

        # Also serve some backlog if price is very cheap
        if current_price < self.avg_price * 0.85 and backlog_mwh > 0:
            backlog_serve = min(backlog_mwh / 1.0, battery_power_mw * 0.5)
            flex_serve_mw += backlog_serve
    else:
        # Expensive hour: defer flex compute unless backlog is urgent
        if oldest_backlog_age_h > 18:
            # Backlog is very old, must serve it
            flex_serve_mw = min(arriving_flex_mw + backlog_mwh, battery_power_mw)
        else:
            # Defer new arrivals, may discharge battery if needed
            flex_serve_mw = 0.0

        # Discharge battery if price is very high and we have charge
        if current_price > price_threshold * 1.2 and battery_soc_mwh > battery_capacity_mwh * 0.2:
            battery_mw = -min(battery_power_mw * 0.8, battery_soc_mwh)

    return flex_serve_mw, battery_mw
