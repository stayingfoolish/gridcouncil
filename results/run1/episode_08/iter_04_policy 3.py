class Policy:
  def __init__(self):
    # Discharge/charge physical limits
    self.max_discharge_rate = 10
    self.max_charge_rate = 12

    # Window-based planning parameters
    self.lookahead_window = 4  # Look 4 timesteps ahead
    self.price_history_length = 5
    self.momentum_threshold = 0.05  # 5% price change significance

    # Dynamic threshold parameters
    self.base_charge_threshold = 0.25
    self.base_discharge_threshold = 0.75

    # Market-aware parameters
    self.high_volatility_damping = 0.7  # Reduce aggressiveness when volatile
    self.low_volatility_boost = 1.15

    # Price levels learned from history
    self.price_history = []
    self.action_history = []

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    # Core state calculations
    battery_fill_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    # Track price history
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.price_history_length:
      self.price_history.pop(0)

    # Calculate price momentum (directional trend)
    if len(self.price_history) >= 2:
      recent_price_change = (current_grid_buy_price - self.price_history[0]) / self.price_history[0]
      price_momentum = recent_price_change / self.price_history_length
    else:
      price_momentum = 0

    # Calculate price volatility locally
    if len(self.price_history) > 1:
      avg_price = sum(self.price_history) / len(self.price_history)
      volatility = sum(abs(p - avg_price) for p in self.price_history) / (avg_price * len(self.price_history))
    else:
      volatility = 0

    # Dynamic thresholds based on market conditions
    volatility_factor = self.high_volatility_damping if volatility > 0.08 else self.low_volatility_boost
    charge_threshold = self.base_charge_threshold * (1 + price_momentum)
    discharge_threshold = self.base_discharge_threshold * (1 - price_momentum)

    # DECISION SCORING SYSTEM: Rank all possible actions
    action_scores = {}

    # Score charging decision
    pv_charging_score = 0
    if current_pv_generation_kw > current_demand_kw + 1.0:
      excess_pv = current_pv_generation_kw - current_demand_kw
      if battery_fill_ratio < 0.90:
        pv_charging_score = excess_pv * 10 * (0.9 - battery_fill_ratio)

    opportunity_charging_score = 0
    if current_grid_buy_price < 0.10:  # Low price opportunity
      if battery_fill_ratio < charge_threshold:
        opportunity_charging_score = (0.10 - current_grid_buy_price) / 0.10 * 15 * volatility_factor

    action_scores['charge'] = max(pv_charging_score, opportunity_charging_score)

    # Score discharging decision
    price_margin = (current_grid_sell_price - current_grid_buy_price) / current_grid_buy_price if current_grid_buy_price > 0 else 0

    profit_discharge_score = 0
    if price_margin > 0.12 and battery_fill_ratio > discharge_threshold:  # Good margin
      profit_discharge_score = price_margin * 100 * battery_fill_ratio * volatility_factor

    supply_discharge_score = 0
    if current_demand_kw > current_pv_generation_kw + 2.0:
      deficit = current_demand_kw - current_pv_generation_kw
      if battery_fill_ratio > 0.30:
        supply_discharge_score = deficit * 8

    action_scores['discharge'] = max(profit_discharge_score, supply_discharge_score)

    # Score holding
    action_scores['hold'] = 0.5  # Baseline for inaction

    # Execute highest-scoring action
    best_action = max(action_scores, key=action_scores.get)

    if best_action == 'charge' and action_scores['charge'] > 2:
      charge_power = min(self.max_charge_rate * volatility_factor, available_capacity * 0.8)
      charge_power = min(charge_power, max(0, action_scores['charge'] / 10))
      return max(0.1, charge_power)

    elif best_action == 'discharge' and action_scores['discharge'] > 2:
      discharge_power = min(self.max_discharge_rate * volatility_factor, current_energy_stored_kwh * 0.7)
      discharge_power = min(discharge_power, max(0, action_scores['discharge'] / 10))
      return -max(0.1, discharge_power)

    return 0.0
