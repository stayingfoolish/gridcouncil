class Policy:
  def __init__(self):
    self.max_discharge_rate = 10
    self.max_charge_rate = 12

    # Core state tracking
    self.price_history = []
    self.soc_history = []
    self.cost_history = []
    self.action_history = []

    # Adaptive parameters
    self.min_lookback = 8
    self.margin_threshold = 0.05
    self.dynamic_threshold_base = 0.05
    self.adaptive_aggressiveness = 1.0

    # Cost trajectory parameters
    self.cost_momentum = 0.0
    self.recent_success_rate = 0.5

    # Battery config
    self.optimal_soc_charge = 0.75
    self.optimal_soc_discharge = 0.30
    self.critical_soc_min = 0.15
    self.critical_soc_max = 0.95

  def update_cost_trajectory(self, current_cost, previous_cost):
    if len(self.cost_history) >= 2:
      cost_delta = current_cost - previous_cost
      self.cost_momentum = 0.7 * self.cost_momentum + 0.3 * cost_delta

    self.cost_history.append(current_cost)
    if len(self.cost_history) > 20:
      self.cost_history.pop(0)

  def calculate_margin_capture_opportunity(self, buy_price, sell_price, historical_prices):
    if not historical_prices or len(historical_prices) < 3:
      return 0

    avg_price = sum(historical_prices) / len(historical_prices)
    max_price = max(historical_prices)
    min_price = min(historical_prices)

    if sell_price >= max_price * 0.95:
      discharge_margin = (sell_price - avg_price) / avg_price
      return max(0, discharge_margin) * 100

    if buy_price <= min_price * 1.05:
      future_upside = (max_price - buy_price) / buy_price
      return max(0, future_upside) * 100

    return 0

  def should_trade_based_on_cost_improvement(self, current_cost, projected_hold_cost, projected_action_cost):
    cost_savings = projected_hold_cost - projected_action_cost
    min_savings_threshold = abs(current_cost) * 0.02

    return cost_savings > min_savings_threshold

  def adapt_thresholds_to_success(self):
    if len(self.action_history) >= 5:
      recent_success = sum(self.action_history[-5:]) / 5.0

      if recent_success > 0.6:
        self.adaptive_aggressiveness = min(1.5, self.adaptive_aggressiveness + 0.05)
        self.margin_threshold = max(0.02, self.margin_threshold - 0.005)
      elif recent_success < 0.4:
        self.adaptive_aggressiveness = max(0.7, self.adaptive_aggressiveness - 0.05)
        self.margin_threshold = min(0.12, self.margin_threshold + 0.01)

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    battery_fill_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    self.price_history.append(current_grid_buy_price)
    self.soc_history.append(battery_fill_ratio)
    if len(self.price_history) > self.min_lookback:
      self.price_history.pop(0)
      self.soc_history.pop(0)

    self.adapt_thresholds_to_success()

    avg_buy_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_grid_buy_price
    price_range = max(self.price_history) - min(self.price_history) if self.price_history else 0

    action_scores = {'hold': 0.5}

    discharge_score = 0

    sell_premium = (current_grid_sell_price - avg_buy_price) / avg_buy_price if avg_buy_price > 0 else 0
    if sell_premium > self.margin_threshold and battery_fill_ratio > self.critical_soc_min:
      discharge_score = max(discharge_score, sell_premium * 120 * (1 + battery_fill_ratio) * self.adaptive_aggressiveness)

    if current_demand_kw > current_pv_generation_kw + 1.5 and battery_fill_ratio > 0.25:
      supply_score = (current_demand_kw - current_pv_generation_kw) * 8
      discharge_score = max(discharge_score, supply_score)

    if battery_fill_ratio > 0.88:
      rebalance_score = (battery_fill_ratio - self.optimal_soc_discharge) * 30
      discharge_score = max(discharge_score, rebalance_score)

    action_scores['discharge'] = discharge_score

    charge_score = 0

    buy_discount = (avg_buy_price - current_grid_buy_price) / avg_buy_price if avg_buy_price > 0 else 0
    if buy_discount > self.margin_threshold and available_capacity > 2.0:
      charge_score = max(charge_score, buy_discount * 120 * (1 + (1 - battery_fill_ratio)) * self.adaptive_aggressiveness)

    if current_pv_generation_kw > current_demand_kw + 2.0 and battery_fill_ratio < 0.90:
      excess_pv = current_pv_generation_kw - current_demand_kw
      pv_score = excess_pv * 15 * (1 - battery_fill_ratio)
      charge_score = max(charge_score, pv_score)

    if battery_fill_ratio < 0.25:
      rebalance_score = (self.optimal_soc_charge - battery_fill_ratio) * 25
      charge_score = max(charge_score, rebalance_score)

    action_scores['charge'] = charge_score

    best_action = max(action_scores, key=action_scores.get)
    best_score = action_scores[best_action]

    if best_action == 'charge' and best_score > 2.0:
      charge_power = min(
        self.max_charge_rate * self.adaptive_aggressiveness,
        available_capacity * 0.85,
        best_score / 12
      )
      action_taken = min(charge_power, max(0.5, charge_power))
      self.action_history.append(1 if buy_discount > 0 else 0)
      return action_taken

    elif best_action == 'discharge' and best_score > 2.0:
      discharge_power = min(
        self.max_discharge_rate * self.adaptive_aggressiveness,
        current_energy_stored_kwh * 0.75,
        best_score / 12
      )
      action_taken = -min(discharge_power, max(0.5, discharge_power))
      self.action_history.append(1 if sell_premium > 0 else 0)
      return action_taken

    self.action_history.append(0)
    return 0.0
