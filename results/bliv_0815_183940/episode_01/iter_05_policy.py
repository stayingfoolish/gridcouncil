from collections import deque
import numpy as np
from typing import Tuple, List

class Policy:
    def __init__(self):
        self.max_charge_power = 10
        self.max_discharge_power = 5
        self.battery_capacity = 50
        
        self.price_history = deque(maxlen=168)
        self.demand_history = deque(maxlen=168)
        
        self.regime_soc_targets = {
            'cheap_period': 0.95,
            'expensive_period': 0.25,
            'rising_trend': 0.80,
            'falling_trend': 0.30,
            'volatile': 0.60
        }
        
        self.cost_percentile_thresholds = {
            'charge_trigger': 0.30,
            'discharge_trigger': 0.75
        }
        
        self.step_counter = 0

    def _calculate_cost_percentile(self, current_price: float) -> float:
        if len(self.price_history) < 20:
            return 0.5
        
        prices = list(self.price_history)
        sorted_prices = sorted(prices)
        
        rank = sum(1 for p in sorted_prices if p <= current_price) / len(sorted_prices)
        return rank

    def _forecast_demand_pattern(self) -> dict:
        if len(self.demand_history) < 24:
            return {'demand_level': 'unknown', 'forecast_avg': 0, 'is_peak': False}
        
        recent_24h = list(self.demand_history)[-24:]
        current_demand = recent_24h[-1] if recent_24h else 0
        avg_demand_24h = sum(recent_24h) / len(recent_24h)
        
        is_peak = current_demand > avg_demand_24h * 1.2
        demand_level = 'peak' if is_peak else 'normal'
        
        return {
            'demand_level': demand_level,
            'forecast_avg': avg_demand_24h,
            'is_peak': is_peak,
            'current': current_demand
        }

    def _identify_cost_regime(self, current_price: float) -> str:
        if len(self.price_history) < 20:
            return 'volatile'
        
        recent = list(self.price_history)[-20:]
        price_slope = (recent[-1] - recent[0]) / (recent[0] + 1e-6)
        price_volatility = np.std(recent) / (np.mean(recent) + 1e-6)
        
        cost_percentile = self._calculate_cost_percentile(current_price)
        
        if cost_percentile < 0.25:
            return 'cheap_period'
        elif cost_percentile > 0.75:
            return 'expensive_period'
        elif price_slope > 0.03:
            return 'rising_trend'
        elif price_slope < -0.03:
            return 'falling_trend'
        else:
            return 'volatile'

    def _simple_forecast(self, horizon: int) -> List[float]:
        if len(self.price_history) < 3:
            return [list(self.price_history)[-1] if self.price_history else 50] * horizon
        
        prices = list(self.price_history)
        alpha = 0.3
        forecast = []
        current = prices[-1]
        trend = (prices[-1] - prices[-3]) / 2
        
        for _ in range(horizon):
            current = alpha * current + (1 - alpha) * (current + trend * 0.8)
            current = max(10, min(100, current))
            forecast.append(current)
        
        return forecast

    def _should_charge(self,
        current_price: float,
        regime: str,
        current_soc: float,
        max_available: float,
        demand_info: dict,
        future_prices: List[float]
    ) -> Tuple[bool, float]:
        
        if max_available < 0.5:
            return False, 0.0
        
        cost_percentile = self._calculate_cost_percentile(current_price)
        target_soc = self.regime_soc_targets.get(regime, 0.5)
        
        if current_soc > 0.90:
            return False, 0.0
        
        if cost_percentile < self.cost_percentile_thresholds['charge_trigger']:
            power = min(max_available, self.max_charge_power)
            return True, power
        
        if len(self.price_history) >= 3:
            recent_slope = (list(self.price_history)[-1] - list(self.price_history)[-3]) / 3
            if recent_slope < 0 and current_soc < target_soc * 1.1:
                power = min(max_available * 0.7, self.max_charge_power * 0.6)
                return True, power
        
        return False, 0.0

    def _should_discharge(self,
        current_price: float,
        regime: str,
        current_soc: float,
        max_available: float,
        demand_info: dict,
        future_prices: List[float]
    ) -> Tuple[bool, float]:
        
        if max_available < 0.5:
            return False, 0.0
        
        cost_percentile = self._calculate_cost_percentile(current_price)
        target_soc = self.regime_soc_targets.get(regime, 0.5)
        
        if current_soc < 0.15:
            return False, 0.0
        
        if cost_percentile > self.cost_percentile_thresholds['discharge_trigger']:
            power = min(max_available, self.max_discharge_power)
            return True, power
        
        if demand_info['is_peak'] and current_soc > target_soc:
            power = min(max_available * 0.5, self.max_discharge_power * 0.5)
            return True, power
        
        if len(self.price_history) >= 3:
            recent_slope = (list(self.price_history)[-1] - list(self.price_history)[-3]) / 3
            if recent_slope > 0 and current_soc > target_soc * 0.9:
                power = min(max_available * 0.6, self.max_discharge_power * 0.7)
                return True, power
        
        return False, 0.0

    def take_action(self,
        current_energy_stored_kwh: float,
        current_pv_generation_kw: float,
        current_demand_kw: float,
        current_grid_buy_price: float,
        current_grid_sell_price: float,
        battery_capacity_kwh: float,
    ) -> float:
        
        self.battery_capacity = battery_capacity_kwh
        self.price_history.append(current_grid_buy_price)
        self.demand_history.append(current_demand_kw)
        self.step_counter += 1
        
        soc = current_energy_stored_kwh / battery_capacity_kwh
        
        energy_to_full = battery_capacity_kwh - current_energy_stored_kwh
        max_charge_available = min(self.max_charge_power, energy_to_full)
        max_discharge_available = min(self.max_discharge_power, current_energy_stored_kwh)
        
        regime = self._identify_cost_regime(current_grid_buy_price)
        demand_info = self._forecast_demand_pattern()
        
        future_prices = self._simple_forecast(24) if len(self.price_history) >= 5 else [current_grid_buy_price] * 24
        
        should_charge, charge_power = self._should_charge(
            current_grid_buy_price,
            regime,
            soc,
            max_charge_available,
            demand_info,
            future_prices
        )
        
        should_discharge, discharge_power = self._should_discharge(
            current_grid_sell_price,
            regime,
            soc,
            max_discharge_available,
            demand_info,
            future_prices
        )
        
        if should_discharge:
            return -discharge_power
        
        if should_charge:
            return charge_power
        
        return 0.0
