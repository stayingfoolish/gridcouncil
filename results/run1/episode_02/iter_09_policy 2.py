import numpy as np
from collections import deque

class Policy:
  def __init__(self):
    self.reserve_soc = 0.20
    self.aggressive_charge_rate_kw = 3.5
    self.aggressive_discharge_rate_kw = 3.5
    self.regret_threshold = 0.15
    self.regret_amplifier = 2.5
    self.max_regret_history = 48
    self.forecast_confidence_baseline = 0.4
    self.spread_zone_multiplier = 0.7

    self.regret_history = deque(maxlen=self.max_regret_history)
    self.price_history = deque(maxlen=24)
    self.forecast_errors_1h = deque(maxlen=24)
    self.forecast_errors_6h = deque(maxlen=24)
    self.forecast_errors_24h = deque(maxlen=24)
    self.spread_history = deque(maxlen=24)

    self.last_executed_spread = 0.0
    self.timestep = 0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.timestep += 1

    current_spread = current_grid_sell_price - current_grid_buy_price
    self.spread_history.append(current_spread)
    self.price_history.append(current_grid_buy_price)

    if len(self.price_history) < 2:
      return 0.0

    available_capacity = battery_capacity_kwh * (1.0 - self.reserve_soc)
    available_energy = current_energy_stored_kwh - (battery_capacity_kwh * self.reserve_soc)
    available_energy = max(0, available_energy)

    forecast_1h = self._forecast_price(1)
    forecast_6h = self._forecast_price(6)
    forecast_24h = self._forecast_price(24)

    volatility = self._calculate_volatility()

    recent_regret_score = np.mean(list(self.regret_history)) if len(self.regret_history) > 0 else 0.0
    regret_boost = 1.0 + (recent_regret_score * self.regret_amplifier)

    if len(self.price_history) >= 4:
      q1 = np.percentile(list(self.price_history), 25)
      q3 = np.percentile(list(self.price_history), 75)
      median = np.percentile(list(self.price_history), 50)
      iqr = q3 - q1
    else:
      q1 = current_grid_buy_price
      q3 = current_grid_buy_price
      median = current_grid_buy_price
      iqr = 0.01

    charge_threshold = median - self.spread_zone_multiplier * max(iqr, 0.01)
    discharge_threshold = median + self.spread_zone_multiplier * max(iqr, 0.01)

    charge_opportunity = max(0, (forecast_6h - current_grid_buy_price) / (current_grid_buy_price + 0.01))
    discharge_opportunity = max(0, (current_grid_sell_price - forecast_6h) / (current_grid_sell_price + 0.01))

    confidence_1h = self.forecast_confidence_baseline * (1.0 / (1.0 + np.mean(list(self.forecast_errors_1h)) if len(self.forecast_errors_1h) > 0 else 0.3))
    confidence_6h = self.forecast_confidence_baseline * (1.0 / (1.0 + np.mean(list(self.forecast_errors_6h)) if len(self.forecast_errors_6h) > 0 else 0.3))
    confidence_24h = self.forecast_confidence_baseline * (1.0 / (1.0 + np.mean(list(self.forecast_errors_24h)) if len(self.forecast_errors_24h) > 0 else 0.3))

    alignment_bonus = 1.0
    if abs(forecast_1h - forecast_6h) < volatility * 0.1:
      alignment_bonus += 0.3

    action_kw = 0.0

    if charge_opportunity > 0.01 and current_grid_buy_price < charge_threshold and current_energy_stored_kwh < battery_capacity_kwh * 0.95:
      confidence_adjusted = charge_opportunity * (confidence_1h + confidence_6h) / 2.0
      action_intensity = min(1.0, confidence_adjusted * regret_boost * alignment_bonus)
      action_kw = action_intensity * self.aggressive_charge_rate_kw
      action_kw = min(action_kw, (battery_capacity_kwh - current_energy_stored_kwh) / 0.25)

    elif discharge_opportunity > 0.01 and current_grid_sell_price > discharge_threshold and available_energy > 0.5:
      confidence_adjusted = discharge_opportunity * (confidence_6h + confidence_24h) / 2.0
      action_intensity = min(1.0, confidence_adjusted * regret_boost * alignment_bonus)
      action_kw = -action_intensity * self.aggressive_discharge_rate_kw
      action_kw = max(action_kw, -available_energy / 0.25)

    else:
      if current_grid_buy_price < charge_threshold * 0.95 and current_energy_stored_kwh < battery_capacity_kwh * 0.85:
        opportunity_ratio = max(0, (forecast_6h - current_grid_buy_price) / (current_grid_buy_price + 0.01))
        confidence_adjusted = opportunity_ratio * confidence_6h
        action_intensity = min(1.0, confidence_adjusted * regret_boost)
        action_kw = action_intensity * self.aggressive_charge_rate_kw * 0.5
        action_kw = min(action_kw, (battery_capacity_kwh - current_energy_stored_kwh) / 0.25)

      elif current_grid_sell_price > discharge_threshold * 1.05 and available_energy > 0.5:
        opportunity_ratio = max(0, (current_grid_sell_price - forecast_6h) / (current_grid_sell_price + 0.01))
        confidence_adjusted = opportunity_ratio * confidence_6h
        action_intensity = min(1.0, confidence_adjusted * regret_boost)
        action_kw = -action_intensity * self.aggressive_discharge_rate_kw * 0.5
        action_kw = max(action_kw, -available_energy / 0.25)

    executed_spread = 0.0
    if action_kw > 0.1:
      executed_spread = current_grid_buy_price
    elif action_kw < -0.1:
      executed_spread = current_grid_sell_price

    regret = max(0, (current_spread - executed_spread) * abs(action_kw) / (self.aggressive_charge_rate_kw + 0.01))
    self.regret_history.append(regret)
    self.last_executed_spread = executed_spread

    action_kw = np.clip(action_kw, -self.aggressive_discharge_rate_kw, self.aggressive_charge_rate_kw)

    return float(action_kw)

  def _forecast_price(self, horizon_hours: int) -> float:
    if len(self.price_history) < 2:
      return np.mean(list(self.price_history)) if len(self.price_history) > 0 else 0.5

    prices = np.array(list(self.price_history))
    trend = np.polyfit(np.arange(len(prices)), prices, 1)[0]
    seasonal_factor = 1.0 + 0.1 * np.sin(2 * np.pi * self.timestep / 24.0)
    forecast = prices[-1] + trend * (horizon_hours / 24.0) * seasonal_factor
    return float(np.clip(forecast, max(prices) * 0.5, max(prices) * 1.5))

  def _calculate_volatility(self) -> float:
    if len(self.price_history) < 2:
      return 0.1
    prices = np.array(list(self.price_history))
    volatility = np.std(prices) if len(prices) > 1 else 0.1
    return max(volatility, 0.01)
