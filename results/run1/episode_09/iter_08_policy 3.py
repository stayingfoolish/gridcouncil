class Policy:
  def __init__(self):
    self.price_history = []
    self.action_history = []
    self.performance_window = 20
    self.adaptation_rate = 0.15

    # Adaptive thresholds learned from recent success
    self.charge_threshold = 0.50  # Buy below this percentile
    self.discharge_threshold = 0.65  # Sell above this percentile
    self.min_action_kw = 0.15  # Lowered from 0.3 to enable more frequent trading

  def get_price_percentile(self, price_history, current_price, window=30):
    """Where does current price rank in recent history?"""
    if len(price_history) < window:
      recent = price_history
    else:
      recent = price_history[-window:]

    sorted_prices = sorted(recent)
    rank = sum(1 for p in sorted_prices if p <= current_price) / len(sorted_prices)
    return rank

  def learn_from_recent_performance(self):
    """Adjust thresholds based on what worked in the last N actions"""
    if len(self.action_history) < 10:
      return

    recent_actions = self.action_history[-self.performance_window:]
    successful_charges = [a for a in recent_actions if a['action'] > 0 and a['cost_impact'] < 0]
    successful_discharges = [a for a in recent_actions if a['action'] < 0 and a['cost_impact'] < 0]

    if len(successful_charges) > 2:
      avg_charge_percentile = sum(a['price_percentile'] for a in successful_charges) / len(successful_charges)
      self.charge_threshold = avg_charge_percentile + self.adaptation_rate * (0.50 - avg_charge_percentile)

    if len(successful_discharges) > 2:
      avg_discharge_percentile = sum(a['price_percentile'] for a in successful_discharges) / len(successful_discharges)
      self.discharge_threshold = avg_discharge_percentile + self.adaptation_rate * (0.65 - avg_discharge_percentile)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    MAX_CHARGE = 10.0
    MAX_DISCHARGE = 8.0

    soc = current_energy_stored_kwh / battery_capacity_kwh

    # Safety guardrails only (not decision gates)
    if soc < 0.05:
      return min(MAX_CHARGE, battery_capacity_kwh - current_energy_stored_kwh)
    if soc > 0.95:
      return -min(MAX_DISCHARGE, current_energy_stored_kwh)

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > 96:
      self.price_history.pop(0)

    self.learn_from_recent_performance()

    # Decision logic: Buy low, sell high (absolute, not relative)
    buy_percentile = self.get_price_percentile(self.price_history, current_grid_buy_price, window=30)
    sell_percentile = self.get_price_percentile(self.price_history, current_grid_sell_price, window=30)

    action = 0.0

    # CHARGE: When current buy price is in the lower half of history
    if buy_percentile < self.charge_threshold and soc < 0.85:
      space = battery_capacity_kwh - current_energy_stored_kwh
      # Aggression scales inversely with price percentile (lower price = charge harder)
      aggression = 0.5 + (1.0 - buy_percentile) * 0.5
      charge = min(MAX_CHARGE * aggression, space, current_pv_generation_kw + 5.0)
      if charge > self.min_action_kw:
        action = charge

    # DISCHARGE: When current sell price is in the upper half of history
    elif sell_percentile > self.discharge_threshold and soc > 0.20:
      # Aggression scales with price percentile (higher price = discharge harder)
      aggression = 0.5 + (sell_percentile - 0.5) * 0.5
      discharge = min(MAX_DISCHARGE * aggression, current_energy_stored_kwh)
      if discharge > self.min_action_kw:
        action = -discharge

    # Opportunistic demand response (secondary)
    elif current_demand_kw > current_pv_generation_kw + 2.0 and soc > 0.25:
      if current_grid_sell_price > 0.0:  # Only discharge when we can "benefit" (avoid buying)
        discharge = min(MAX_DISCHARGE * 0.4, current_energy_stored_kwh)
        if discharge > self.min_action_kw:
          action = -discharge

    # Record for learning
    action_record = {
      'action': action,
      'price_percentile': buy_percentile if action > 0 else sell_percentile,
      'soc': soc,
      'timestamp': len(self.price_history),
      'cost_impact': 0  # Will be computed externally
    }
    self.action_history.append(action_record)
    if len(self.action_history) > self.performance_window * 2:
      self.action_history.pop(0)

    return action
