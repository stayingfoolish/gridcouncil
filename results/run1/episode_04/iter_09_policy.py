import numpy as np
from collections import deque
from typing import Tuple

class Policy:
  def __init__(self):
    # Price Forecasting (24-hour ahead)
    self.FORECAST_WINDOW = 24
    self.SEASONAL_COMPONENTS = 7  # 7 distinct daily patterns
    self.ARIMA_ORDER = (2, 1, 2)
    self.FORECAST_LOOKBACK = 336  # 2 weeks

    # Bayesian Confidence Tracking
    self.FORECAST_ERROR_ALPHA = 2.0  # Prior confidence in forecast
    self.FORECAST_ERROR_BETA = 2.0
    self.MIN_CONFIDENCE_THRESHOLD = 0.4
    self.CONFIDENCE_UPDATE_RATE = 0.05

    # Arbitrage Window Detection
    self.ARBITRAGE_MIN_GAP = 0.15  # 15% price swing required
    self.ARBITRAGE_HORIZON = 6  # Look 6 hours ahead for discharge
    self.CHARGING_EFFICIENCY = 0.92
    self.DISCHARGING_EFFICIENCY = 0.95

    # Battery Constraints
    self.MIN_SAFE_BATTERY_LEVEL = 0.15
    self.MAX_SAFE_BATTERY_LEVEL = 0.90
    self.EMERGENCY_RESERVE = 0.05

    # Historical tracking
    self.price_history = deque(maxlen=self.FORECAST_LOOKBACK)
    self.demand_history = deque(maxlen=self.FORECAST_LOOKBACK)
    self.pv_history = deque(maxlen=self.FORECAST_LOOKBACK)
    self.forecast_errors = deque(maxlen=24)

    # Forecast state
    self.forecast_confidence = 0.5
    self.seasonal_factors = [1.0] * self.SEASONAL_COMPONENTS
    self.last_price_forecast = None
    self.last_min_price_index = 0
    self.last_max_price_index = 0

  def _forecast_24h_prices(self) -> Tuple[np.ndarray, np.ndarray]:
    """Forecast next 24 hours of prices with confidence bounds."""
    if len(self.price_history) < 48:
      return np.array([np.mean(list(self.price_history))] * 24), np.array([0.3] * 24)

    prices = np.array(list(self.price_history))

    # Seasonal decomposition
    hourly_idx = len(self.price_history) % 24
    day_idx = (len(self.price_history) // 24) % self.SEASONAL_COMPONENTS

    # Simple exponential smoothing with seasonal adjustment
    base_trend = np.mean(prices[-12:])
    seasonal_adjustment = self.seasonal_factors[day_idx]

    # ARIMA-like differencing
    if len(prices) >= 2:
      first_diff = np.diff(prices[-48:])
      momentum = np.mean(first_diff[-6:])
    else:
      momentum = 0

    # Generate 24-hour forecast
    forecast = np.zeros(24)
    confidence_bounds = np.zeros(24)

    for h in range(24):
      current_hour = (hourly_idx + h) % 24
      current_day = (day_idx + (hourly_idx + h) // 24) % self.SEASONAL_COMPONENTS

      # Trend + seasonal + momentum
      trend_component = base_trend + (momentum * (h / 12))
      seasonal_component = self.seasonal_factors[current_day] * (0.9 + 0.2 * np.sin(current_hour * np.pi / 12))

      forecast[h] = trend_component * seasonal_component

      # Confidence decreases with forecast horizon
      base_confidence = self.forecast_confidence
      horizon_decay = np.exp(-0.1 * h)
      confidence_bounds[h] = base_confidence * horizon_decay + (1 - base_confidence) * 0.2

    # Normalize prices to be positive
    forecast = np.maximum(forecast, 0.01)

    return forecast, confidence_bounds

  def _find_arbitrage_opportunity(self, forecast: np.ndarray, confidence: np.ndarray) -> Tuple[int, int, float]:
    """Find best charge/discharge timing in 24-hour window."""
    # Find minimum price (charging opportunity)
    weighted_forecast = forecast / confidence
    min_idx = np.argmin(weighted_forecast)
    min_price = forecast[min_idx]

    # Find maximum price after minimum (discharge opportunity)
    future_prices = weighted_forecast[min_idx + self.ARBITRAGE_HORIZON:]
    if len(future_prices) > 0:
      max_idx_in_future = np.argmax(future_prices)
      max_idx = min_idx + self.ARBITRAGE_HORIZON + max_idx_in_future
      max_price = forecast[max_idx]
    else:
      max_idx = min_idx + 1
      max_price = forecast[max_idx]

    arbitrage_spread = (max_price - min_price) / (min_price + 0.01)

    return min_idx, max_idx, arbitrage_spread

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines action based on predicted arbitrage opportunities."""

    # Update histories
    self.price_history.append(current_grid_buy_price)
    self.demand_history.append(current_demand_kw)
    self.pv_history.append(current_pv_generation_kw)

    # Calculate battery state
    battery_soc = current_energy_stored_kwh / max(battery_capacity_kwh, 0.01)

    # Get 24-hour price forecast
    forecast, confidence = self._forecast_24h_prices()
    self.last_price_forecast = forecast

    # Find arbitrage opportunity
    min_idx, max_idx, spread = self._find_arbitrage_opportunity(forecast, confidence)

    action_kw = 0.0

    # Decision logic based on arbitrage opportunity
    if min_idx == 0 and spread > self.ARBITRAGE_MIN_GAP:
      # We're at charging opportunity now
      if battery_soc < self.MAX_SAFE_BATTERY_LEVEL:
        # Charge aggressively if confident in arbitrage
        charge_magnitude = 10.0 * (confidence[0] ** 0.5)
        action_kw = min(charge_magnitude, 10.0)

    elif max_idx <= 6 and spread > self.ARBITRAGE_MIN_GAP and battery_soc > 0.4:
      # Discharge opportunity in next 6 hours
      if battery_soc > self.MIN_SAFE_BATTERY_LEVEL + 0.1:
        discharge_magnitude = 5.0 * (confidence[max_idx] ** 0.5)
        action_kw = -min(discharge_magnitude, 5.0)

    else:
      # No clear arbitrage: maintain position or balance demand
      net_flow = current_pv_generation_kw - current_demand_kw
      if net_flow > 1.0 and battery_soc < self.MAX_SAFE_BATTERY_LEVEL:
        action_kw = min(net_flow * 0.8, 3.0)
      elif net_flow < -1.0 and battery_soc > self.MIN_SAFE_BATTERY_LEVEL:
        action_kw = max(net_flow * 0.5, -2.0)

    # Apply hard constraints
    action_kw = max(-5.0, min(10.0, action_kw))

    # Energy conservation
    if action_kw < 0:
      max_discharge = current_energy_stored_kwh - (battery_capacity_kwh * self.EMERGENCY_RESERVE)
      action_kw = max(action_kw, -max_discharge)
    elif action_kw > 0:
      max_charge = (battery_capacity_kwh - current_energy_stored_kwh) / self.CHARGING_EFFICIENCY
      action_kw = min(action_kw, max_charge)

    # Update forecast confidence based on realized price vs forecast
    if self.last_price_forecast is not None and len(self.forecast_errors) > 0:
      error = abs(current_grid_buy_price - self.last_price_forecast[0])
      self.forecast_errors.append(error)
      mean_error = np.mean(list(self.forecast_errors))
      # Bayesian update: improve confidence when errors are low
      error_percentile = mean_error / (np.mean(list(self.price_history)) + 0.01)
      self.forecast_confidence = min(0.8, self.forecast_confidence + self.CONFIDENCE_UPDATE_RATE * (0.2 - error_percentile))

    # Update seasonal factors (every 24 hours)
    if len(self.price_history) % 24 == 0 and len(self.price_history) > 0:
      hour_of_week = (len(self.price_history) // 24) % self.SEASONAL_COMPONENTS
      recent_prices = list(self.price_history)[-24:]
      self.seasonal_factors[hour_of_week] = np.mean(recent_prices) / (np.mean(list(self.price_history)) + 0.01)

    return action_kw
