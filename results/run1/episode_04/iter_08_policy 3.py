import numpy as np
from collections import deque
from typing import Tuple

class Policy:
  def __init__(self):
    """Initializes the policy with historical tracking and adaptive learning."""
    # Demand/Generation Pattern Recognition
    self.PATTERN_LOOKBACK_HOURS = 168
    self.DEMAND_SPIKE_PERCENTILE = 75
    self.PV_DROP_PERCENTILE = 25

    # Price Regime Detection
    self.ACCELERATION_THRESHOLD = 0.08
    self.BULL_MARKET_FACTOR = 1.5
    self.BEAR_MARKET_FACTOR = 0.6
    self.MOMENTUM_TREND_WINDOW = 12

    # Adaptive Ensemble Learning
    self.MOMENTUM_INITIAL_WEIGHT = 0.35
    self.MEANREV_INITIAL_WEIGHT = 0.35
    self.SEASONAL_INITIAL_WEIGHT = 0.30
    self.FORECAST_ERROR_DECAY = 0.95
    self.ERROR_THRESHOLD_PERCENTILE = 10

    # Asymmetric Risk
    self.CHARGE_SAFETY_MARGIN = 1.2
    self.DISCHARGE_CAUTION_FACTOR = 0.7
    self.MIN_SAFE_BATTERY_LEVEL = 0.25

    # Aggressive Exploration
    self.EXPLORATION_TRIGGER_HOURS = 1.5
    self.EXPLORATION_TARGET_SOC = 0.50

    # Historical tracking
    self.demand_history = deque(maxlen=self.PATTERN_LOOKBACK_HOURS)
    self.pv_history = deque(maxlen=self.PATTERN_LOOKBACK_HOURS)
    self.price_history = deque(maxlen=max(20, self.MOMENTUM_TREND_WINDOW))
    self.forecast_errors = deque(maxlen=10)

    # Adaptive weights
    self.momentum_weight = self.MOMENTUM_INITIAL_WEIGHT
    self.meanrev_weight = self.MEANREV_INITIAL_WEIGHT
    self.seasonal_weight = self.SEASONAL_INITIAL_WEIGHT

    # Exploration tracking
    self.idle_steps = 0
    self.last_action = 0.0

    # Price statistics
    self.price_mean = 0.0
    self.price_std = 1.0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state."""

    # Update historical records
    self.demand_history.append(current_demand_kw)
    self.pv_history.append(current_pv_generation_kw)
    self.price_history.append(current_grid_buy_price)

    # Update price statistics for Z-score calculation
    if len(self.price_history) >= 10:
      self.price_mean = np.mean(list(self.price_history))
      self.price_std = max(np.std(list(self.price_history)), 0.01)

    # Calculate Z-score for current price
    price_zscore = (current_grid_buy_price - self.price_mean) / self.price_std if self.price_std > 0 else 0.0

    # Calculate battery state of charge (SOC)
    battery_soc = current_energy_stored_kwh / max(battery_capacity_kwh, 0.01)

    # 1. Demand/Generation Intelligence Layer
    demand_anomaly = 0.0
    pv_anomaly = 0.0

    if len(self.demand_history) >= 24:
      demand_median = np.median(list(self.demand_history))
      demand_std = max(np.std(list(self.demand_history)), 0.01)
      demand_anomaly = (current_demand_kw - demand_median) / demand_std

    if len(self.pv_history) >= 24:
      pv_median = np.median(list(self.pv_history))
      pv_std = max(np.std(list(self.pv_history)), 0.01)
      pv_anomaly = (current_pv_generation_kw - pv_median) / pv_std

    charge_opportunity_score = 1.0 + (demand_anomaly * 0.3)
    discharge_opportunity_score = 1.0 + (pv_anomaly * 0.2)

    # 2. Price Regime Detection
    regime = "STABLE"
    if len(self.price_history) >= 20:
      recent_5h_slope = (self.price_history[-1] - self.price_history[-5]) / 5 if len(self.price_history) >= 5 else 0
      recent_20h_slope = (self.price_history[-1] - self.price_history[-20]) / 20

      if recent_5h_slope > self.ACCELERATION_THRESHOLD:
        regime = "BULL"
        self.momentum_weight = 0.50
        self.seasonal_weight = 0.20
        self.meanrev_weight = 0.30
      elif recent_5h_slope < -self.ACCELERATION_THRESHOLD:
        regime = "BEAR"
        self.momentum_weight = 0.20
        self.meanrev_weight = 0.50
        self.seasonal_weight = 0.30
      else:
        self.momentum_weight = self.MOMENTUM_INITIAL_WEIGHT
        self.meanrev_weight = self.MEANREV_INITIAL_WEIGHT
        self.seasonal_weight = self.SEASONAL_INITIAL_WEIGHT

    # Normalize weights
    total_weight = self.momentum_weight + self.meanrev_weight + self.seasonal_weight
    if total_weight > 0:
      self.momentum_weight /= total_weight
      self.meanrev_weight /= total_weight
      self.seasonal_weight /= total_weight

    # 3. Asymmetric Decision Logic
    charge_threshold = -0.5 / self.CHARGE_SAFETY_MARGIN
    discharge_threshold = 0.8 * self.DISCHARGE_CAUTION_FACTOR

    # Determine discharge urgency multiplier
    discharge_urgency_multiplier = 1.0
    if battery_soc < 0.25 and price_zscore > 0.5:
      discharge_urgency_multiplier = 0.3
    elif battery_soc > 0.85 and price_zscore < -0.8:
      discharge_urgency_multiplier = 1.5

    # Calculate action magnitudes
    charge_action_magnitude = 10.0 * self.CHARGE_SAFETY_MARGIN * charge_opportunity_score
    discharge_action_magnitude = 5.0 * self.DISCHARGE_CAUTION_FACTOR / max(discharge_opportunity_score, 0.1)

    # Decide action based on price signals and opportunity scores
    action_kw = 0.0

    # Charge decision: lower threshold means easier to charge
    if price_zscore < charge_threshold and battery_soc < 0.95:
      action_kw = min(charge_action_magnitude, 10.0)
      self.idle_steps = 0
    # Discharge decision: higher threshold means harder to discharge
    elif price_zscore > discharge_threshold and battery_soc > self.MIN_SAFE_BATTERY_LEVEL:
      action_kw = -min(discharge_action_magnitude * discharge_urgency_multiplier, 5.0)
      self.idle_steps = 0
    else:
      self.idle_steps += 1

    # 4. Smarter Exploration
    if self.idle_steps >= self.EXPLORATION_TRIGGER_HOURS:
      distance_to_target = abs(battery_soc - self.EXPLORATION_TARGET_SOC)

      if distance_to_target > 0.15:
        if battery_soc < self.EXPLORATION_TARGET_SOC:
          action_kw = 3.0
        else:
          action_kw = -2.0
        self.idle_steps = 0

    # Apply constraints
    action_kw = max(-5.0, min(10.0, action_kw))

    # Check energy conservation constraints
    if action_kw < 0:  # Discharging
      available_energy = current_energy_stored_kwh
      max_discharge_energy = available_energy
      max_discharge_power = max_discharge_energy
      action_kw = max(action_kw, -max_discharge_power)

    # Check battery capacity constraints
    if action_kw > 0:  # Charging
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      max_charge_energy = available_capacity
      max_charge_power = max_charge_energy
      action_kw = min(action_kw, max_charge_power)

    self.last_action = action_kw
    return action_kw
