class Policy:
  def __init__(self):
    self.max_discharge_rate = 5
    self.max_charge_rate = 10

    self.price_history = []
    self.soc_history = []
    self.cost_history = []
    self.momentum_history = []

    self.price_volatility = 0.0
    self.volatility_lookback = 12
    self.regime_volatility_threshold = 8.0

    self.momentum = 0.0
    self.trend_strength = 0.0
    self.price_elasticity = 1.0

    self.bundle_depth = 2
    self.min_bundle_improvement = 0.08

    self.arbitrage_weight = 0.6
    self.demand_weight = 0.25
    self.stability_weight = 0.15

    self.optimal_soc = 0.5
    self.critical_soc_min = 0.15
    self.critical_soc_max = 0.95

  def compute_price_momentum_and_trend(self):
    if len(self.price_history) < 4:
      return self.price_history[-1] if self.price_history else 0

    recent_delta = self.price_history[-1] - self.price_history[-3] if len(self.price_history) >= 3 else 0

    if len(self.cost_history) >= 3 and len(self.momentum_history) > 0:
      cost_trend = (self.cost_history[-1] - self.cost_history[-3]) / max(0.01, abs(self.cost_history[-3]))
      self.momentum = 0.65 * self.momentum + 0.35 * recent_delta
      self.trend_strength = 0.8 * self.trend_strength + 0.2 * cost_trend
    else:
      self.momentum = recent_delta

    predicted_price = self.price_history[-1] + self.momentum * 0.7
    return max(0.01, predicted_price)

  def estimate_market_regime(self):
    if len(self.price_history) < self.volatility_lookback:
      return "initializing"

    recent_prices = self.price_history[-self.volatility_lookback:]
    avg_price = sum(recent_prices) / len(recent_prices)
    variance = sum((p - avg_price) ** 2 for p in recent_prices) / len(recent_prices)
    self.price_volatility = variance ** 0.5

    if self.price_volatility > self.regime_volatility_threshold:
      return "volatile"
    elif abs(self.momentum) > avg_price * 0.03:
      return "trending"
    else:
      return "stable"

  def evaluate_action_bundle(self, current_params, action_sequence):
    predicted_price_t1 = self.compute_price_momentum_and_trend()

    if action_sequence[0] > 0:
      cost_t1 = current_params['current_buy_price'] * action_sequence[0]
      cost_t1 += predicted_price_t1 * action_sequence[0] * (-0.98)
    elif action_sequence[0] < 0:
      cost_t1 = -current_params['current_sell_price'] * abs(action_sequence[0])
      cost_t1 *= 0.95
    else:
      cost_t1 = 0

    return cost_t1

  def adapt_weights_to_regime(self, regime):
    if regime == "volatile":
      self.arbitrage_weight = 0.4
      self.demand_weight = 0.45
      self.stability_weight = 0.15
    elif regime == "trending":
      self.arbitrage_weight = 0.75
      self.demand_weight = 0.15
      self.stability_weight = 0.10
    else:
      self.arbitrage_weight = 0.55
      self.demand_weight = 0.30
      self.stability_weight = 0.15

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
    if len(self.price_history) > 20:
      self.price_history.pop(0)
      self.soc_history.pop(0)

    regime = self.estimate_market_regime()
    self.adapt_weights_to_regime(regime)

    predicted_next_price = self.compute_price_momentum_and_trend()

    current_params = {
      'current_buy_price': current_grid_buy_price,
      'current_sell_price': current_grid_sell_price,
      'predicted_next_price': predicted_next_price,
      'battery_fill_ratio': battery_fill_ratio,
      'available_capacity': available_capacity
    }

    bundles = {
      'aggressive_charge': [min(self.max_charge_rate, available_capacity * 0.9), 0],
      'moderate_charge': [min(self.max_charge_rate * 0.6, available_capacity * 0.6), 0],
      'aggressive_discharge': [-min(self.max_discharge_rate, current_energy_stored_kwh * 0.75), 0],
      'moderate_discharge': [-min(self.max_discharge_rate * 0.6, current_energy_stored_kwh * 0.5), 0],
      'hold': [0, 0]
    }

    best_bundle = 'hold'
    best_cost = 0

    for bundle_name, action_seq in bundles.items():
      bundle_cost = self.evaluate_action_bundle(current_params, action_seq)

      if action_seq[0] > 0:
        margin_factor = (current_grid_buy_price - predicted_next_price) / max(0.01, current_grid_buy_price)
        score = self.arbitrage_weight * margin_factor * 50 + self.stability_weight * (1 - battery_fill_ratio)
      elif action_seq[0] < 0:
        margin_factor = (current_grid_sell_price - current_grid_buy_price) / max(0.01, current_grid_buy_price)
        demand_urgency = max(0, current_demand_kw - current_pv_generation_kw) / max(1, current_demand_kw)
        score = self.arbitrage_weight * margin_factor * 50 + self.demand_weight * demand_urgency * 40
      else:
        score = 0.5 + self.stability_weight * 5

      if score > best_cost and battery_fill_ratio > self.critical_soc_min:
        best_cost = score
        best_bundle = bundle_name

    action = bundles[best_bundle][0]

    if action > 0 and current_grid_buy_price < predicted_next_price:
      return min(action, available_capacity * 0.85)
    elif action < 0 and current_grid_sell_price > current_grid_buy_price:
      return action
    elif action == 0:
      return 0.0

    return action
