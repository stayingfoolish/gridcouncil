class Policy:
  def __init__(self):
    self.max_discharge_rate = 5
    self.max_charge_rate = 10

    self.price_history = []
    self.soc_history = []
    self.cost_history = []
    self.demand_history = []
    self.pv_history = []

    # Dynamic state machine (replaces static percentiles)
    self.price_state = "fair"  # crash, cheap, fair, expensive, spike
    self.state_transition_threshold = 0.15  # 15% shift triggers state change

    # Adaptive margins based on volatility
    self.volatility_weight = 0.4  # Influence of volatility on margins
    self.base_charge_margin = 0.15  # 15% below state threshold to trigger charge
    self.base_discharge_margin = 0.12  # 12% above state threshold to trigger discharge

    # Demand-aware charging
    self.pv_charge_bonus = 0.3  # Increase charge aggressiveness when PV > demand
    self.demand_discharge_bonus = 0.2  # Increase discharge aggressiveness during high demand

    # Trend acceleration detection
    self.trend_alpha = 0.5  # INCREASED from 0.3 for faster response
    self.acceleration_factor = 0.0
    self.accel_alpha = 0.4

    # Price state history for mean reversion detection
    self.price_mean_lookback = 24
    self.mean_reversion_threshold = 0.25

    # Utilization enforcement
    self.cycles_since_discharge = 0
    self.max_hold_cycles = 5  # REDUCED from 8 to improve utilization
    self.min_discharge_soc = 0.30  # REDUCED from 0.35
    self.critical_charge_soc = 0.20  # REDUCED from 0.25

    # Price tracking
    self.price_volatility = 0.0
    self.lookback_window = 12

  def classify_price_state(self, current_price, forecast):
    """Classify price into discrete states based on statistics and momentum"""
    if len(self.price_history) < self.price_mean_lookback:
      return "fair", current_price

    # Calculate distribution statistics
    recent_prices = self.price_history[-self.price_mean_lookback:]
    mean_price = sum(recent_prices) / len(recent_prices)
    sorted_prices = sorted(recent_prices)
    percentile_25 = sorted_prices[len(sorted_prices) // 4]
    percentile_75 = sorted_prices[3 * len(sorted_prices) // 4]

    # Momentum indicator
    price_momentum = (self.price_history[-1] - self.price_history[-2]) / max(0.01, self.price_history[-2])

    # State classification with hysteresis
    if current_price < percentile_25 * 0.85:
      state = "crash"
    elif current_price < percentile_25 * 1.05:
      state = "cheap"
    elif current_price < percentile_75 * 0.95:
      state = "fair"
    elif current_price < percentile_75 * 1.1:
      state = "expensive"
    else:
      state = "spike"

    return state, mean_price

  def calculate_volatility(self):
    """Calculate price volatility with exponential weighting"""
    if len(self.price_history) < self.lookback_window:
      return 0.0

    recent_prices = self.price_history[-self.lookback_window:]
    mean_price = sum(recent_prices) / len(recent_prices)
    variance = sum((p - mean_price) ** 2 for p in recent_prices) / len(recent_prices)
    self.price_volatility = variance ** 0.5
    return self.price_volatility

  def detect_trend_acceleration(self):
    """Detect if price trend is accelerating"""
    if len(self.price_history) < 3:
      return 0.0

    # Calculate recent accelerations
    delta1 = self.price_history[-1] - self.price_history[-2]
    delta2 = self.price_history[-2] - self.price_history[-3]
    acceleration = (delta1 - delta2) / max(0.01, abs(delta2))

    self.acceleration_factor = self.accel_alpha * acceleration + (1 - self.accel_alpha) * self.acceleration_factor
    return self.acceleration_factor

  def get_adaptive_thresholds(self, state, mean_price, volatility):
    """Generate charge/discharge thresholds adapted to price state and volatility"""
    # Base thresholds per state (buy/sell points)
    state_offsets = {
      "crash": {"buy": -0.25, "sell": -0.05},
      "cheap": {"buy": -0.12, "sell": 0.05},
      "fair": {"buy": 0.0, "sell": 0.10},
      "expensive": {"buy": 0.08, "sell": 0.20},
      "spike": {"buy": 0.15, "sell": 0.35}
    }

    offset = state_offsets.get(state, state_offsets["fair"])

    # Adjust margins based on volatility (higher volatility = wider margins)
    volatility_adjustment = min(0.1, volatility * self.volatility_weight)

    charge_threshold = mean_price * (1 + offset["buy"] - volatility_adjustment)
    discharge_threshold = mean_price * (1 + offset["sell"] + volatility_adjustment)

    return charge_threshold, discharge_threshold

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

    # Update history
    self.price_history.append(current_grid_buy_price)
    self.soc_history.append(battery_fill_ratio)
    self.pv_history.append(current_pv_generation_kw)
    self.demand_history.append(current_demand_kw)

    if len(self.price_history) > 48:
      self.price_history.pop(0)
      self.soc_history.pop(0)
      self.pv_history.pop(0)
      self.demand_history.pop(0)

    # Calculate metrics
    volatility = self.calculate_volatility()
    acceleration = self.detect_trend_acceleration()

    # Classify price state
    forecast = [current_grid_buy_price] * 6  # Simplified; expand if needed
    price_state, mean_price = self.classify_price_state(current_grid_buy_price, forecast)
    charge_threshold, discharge_threshold = self.get_adaptive_thresholds(price_state, mean_price, volatility)

    # Increment hold counter
    self.cycles_since_discharge += 1

    # Demand-aware modifiers
    excess_pv = max(0, current_pv_generation_kw - current_demand_kw)
    net_export = excess_pv > 0.5  # PV generation exceeds demand
    high_demand = current_demand_kw > 3.0  # High consumption period

    # **DISCHARGE LOGIC** - Aggressive when price favorable or high demand
    if battery_fill_ratio > self.min_discharge_soc:
      # Conditions for discharge
      forced_discharge = self.cycles_since_discharge >= self.max_hold_cycles
      price_favorable = current_grid_sell_price > discharge_threshold
      spike_detected = price_state in ["spike", "expensive"] and acceleration > 0.05
      demand_driven = high_demand and price_favorable

      if forced_discharge or spike_detected or (price_favorable and demand_driven):
        # Adaptive discharge rate based on state
        base_discharge = min(self.max_discharge_rate, current_energy_stored_kwh * 0.7)

        if price_state == "spike":
          action = -base_discharge * 1.2  # 20% boost on spike
        elif high_demand:
          action = -base_discharge * 1.1  # 10% boost during peak demand
        else:
          action = -base_discharge

        self.cycles_since_discharge = 0
        return action

    # **CHARGE LOGIC** - Buy when price low or capture excess PV
    if battery_fill_ratio < 0.92:  # Slightly higher threshold
      # Conditions for charge
      price_attractive = current_grid_buy_price < charge_threshold
      critical_low = battery_fill_ratio < self.critical_charge_soc
      pv_abundant = net_export and battery_fill_ratio < 0.85  # Capture PV surplus
      crash_detected = price_state == "crash" and acceleration < -0.05  # Bottom fishing

      if price_attractive or critical_low or pv_abundant or crash_detected:
        # Adaptive charge rate
        base_charge = min(self.max_charge_rate, available_capacity * 0.8)

        if crash_detected:
          action = base_charge * 1.3  # 30% boost during price crashes
        elif pv_abundant:
          action = base_charge * 1.15  # 15% boost when harvesting PV
        else:
          action = base_charge

        return action

    # **HOLD** - No action
    return 0.0
