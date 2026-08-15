class Policy:
  def __init__(self):
    """Opportunity-scoring strategy with adaptive forecasting windows."""
    self.price_history = []
    self.demand_history = []
    self.max_history = 24  # Extended from 12 to capture daily/multi-period patterns

    self.charge_rate_kw = 2.0
    self.discharge_rate_kw = 2.0

    # Adaptive SOC bounds based on opportunity detection
    self.soc_min = 0.05  # Aggressive lower bound to capture more discharge opportunities
    self.soc_max = 0.95  # Aggressive upper bound for charging
    self.soc_safety_min = 0.10  # Hard safety floor
    self.soc_safety_max = 0.90  # Hard safety ceiling

    # Adaptive thresholds
    self.spread_threshold_aggressive = 1.15  # 15% spread triggers aggressive action
    self.spread_threshold_moderate = 1.08   # 8% spread for moderate action
    self.volatility_window = 6  # Rolling window for volatility calculation
    self.demand_forecast_window = 4

  def calculate_volatility(self):
    """Calculate recent price volatility to adapt aggressiveness."""
    if len(self.price_history) < self.volatility_window:
      return 0.0
    recent = self.price_history[-self.volatility_window:]
    avg = sum(recent) / len(recent)
    variance = sum((p - avg) ** 2 for p in recent) / len(recent)
    return variance ** 0.5

  def calculate_opportunity_score(self, action_type, current_soc, volatility,
                                   spread_ratio, price_momentum, demand_momentum):
    """
    Unified scoring function: returns opportunity strength (-1.0 to 1.0).
    Positive = charge, Negative = discharge.
    """
    score = 0.0

    if action_type == "charge":
      # Charge score increases with: falling prices, PV excess, low SOC, high spread
      if price_momentum < -0.05:  # Prices falling
        score += 0.4 * (-price_momentum / 0.2)  # Stronger the fall, stronger the signal
      if spread_ratio > self.spread_threshold_moderate:  # Grid buying cheaper
        score += 0.3
      if current_soc < 0.5:  # Battery has room
        score += 0.2
      if demand_momentum < 0:  # Demand falling (less immediate need)
        score += 0.1

      score = min(score, 1.0)
      return score

    elif action_type == "discharge":
      # Discharge score increases with: rising prices, high spread, high SOC, demand spike
      if price_momentum > 0.05:  # Prices rising
        score += 0.4 * (price_momentum / 0.2)
      if spread_ratio > self.spread_threshold_aggressive:  # Grid selling valuable
        score += 0.35
      if current_soc > 0.5:  # Battery well-charged
        score += 0.15
      if demand_momentum > 0:  # Demand rising (valuable supply)
        score += 0.1

      score = min(score, 1.0)
      return score

    return 0.0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Opportunity-scoring decision engine."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Track histories
    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
      self.demand_history.pop(0)

    action_kw = 0.0

    if len(self.price_history) >= 4:
      # Calculate momentum signals
      recent_avg = (self.price_history[-1] + self.price_history[-2]) / 2
      earlier_avg = (self.price_history[-3] + self.price_history[-4]) / 2
      price_momentum = (recent_avg - earlier_avg) / (earlier_avg + 0.001)

      demand_avg = sum(self.demand_history[-2:]) / 2
      demand_baseline = sum(self.demand_history[-4:-2]) / 2
      demand_momentum = (demand_avg - demand_baseline) / (demand_baseline + 0.1)

      spread_ratio = current_grid_sell_price / (current_grid_buy_price + 0.001)
      volatility = self.calculate_volatility()

      # Adjust thresholds dynamically: high volatility allows more aggressive action
      aggressive_threshold = self.spread_threshold_aggressive * (1.0 - 0.2 * min(volatility / 0.5, 1.0))

      # Calculate opportunity scores
      charge_score = self.calculate_opportunity_score(
        "charge", current_soc, volatility, spread_ratio, price_momentum, demand_momentum
      )
      discharge_score = self.calculate_opportunity_score(
        "discharge", current_soc, volatility, spread_ratio, price_momentum, demand_momentum
      )

      # Determine dominant action based on highest opportunity score
      if discharge_score > charge_score and discharge_score > 0.3:
        # Discharge opportunity
        soc_floor = self.soc_safety_min if volatility > 0.3 else self.soc_min
        if current_soc > soc_floor:
          discharge_available = min(
            self.discharge_rate_kw * (0.6 + 0.4 * discharge_score),  # Scale with opportunity
            current_energy_stored_kwh - battery_capacity_kwh * soc_floor
          )
          if discharge_available > 0.1:
            return -discharge_available

      elif charge_score > discharge_score and charge_score > 0.3:
        # Charge opportunity
        soc_ceiling = self.soc_safety_max if volatility > 0.3 else self.soc_max
        if current_soc < soc_ceiling:
          charge_available = min(
            self.charge_rate_kw * (0.6 + 0.4 * charge_score),
            battery_capacity_kwh * soc_ceiling - current_energy_stored_kwh
          )
          if charge_available > 0.1:
            return charge_available

    # Fallback: balance demand/PV generation conservatively
    if current_demand_kw > current_pv_generation_kw:
      energy_deficit = current_demand_kw - current_pv_generation_kw
      if current_soc > self.soc_safety_min + 0.05:
        discharge_for_demand = min(
          self.discharge_rate_kw * 0.25,
          energy_deficit,
          current_energy_stored_kwh - battery_capacity_kwh * (self.soc_safety_min + 0.05)
        )
        if discharge_for_demand > 0.05:
          return -discharge_for_demand

    if current_pv_generation_kw > current_demand_kw:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if current_soc < self.soc_safety_max - 0.05:
        charge_from_pv = min(
          self.charge_rate_kw * 0.25,
          pv_excess,
          battery_capacity_kwh * (self.soc_safety_max - 0.05) - current_energy_stored_kwh
        )
        if charge_from_pv > 0.05:
          return charge_from_pv

    return 0.0
