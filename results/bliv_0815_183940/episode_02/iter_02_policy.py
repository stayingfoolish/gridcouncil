import numpy as np
from collections import deque

class Policy:
    def __init__(self):
        """Initializes the policy with predictive price-aware control."""
        
        # Price history tracking
        self.price_history_window = 24
        self.buy_price_history = deque(maxlen=self.price_history_window)
        self.sell_price_history = deque(maxlen=self.price_history_window)
        
        # Hard SOC limits
        self.min_soc_hard_limit = 0.10
        self.max_soc_hard_limit = 0.95
        
        # Power limits
        self.max_charge_power = 8.0
        self.max_discharge_power = 8.0
        
        # Price-based decision thresholds
        self.price_percentile_charge = 25
        self.price_percentile_discharge = 75
        
        # Volatility threshold
        self.min_volatility_threshold = 5.0
        
        # Decay factor for momentum
        self.momentum_decay = 0.9
    
    def _calculate_price_percentile(self, current_price, price_history):
        """Calculate where current price sits in historical distribution."""
        if len(price_history) < 3:
            return 50
        return (np.sum(np.array(price_history) <= current_price) / len(price_history)) * 100
    
    def _calculate_price_momentum(self, price_history):
        """Calculate if prices are trending up (positive) or down (negative)."""
        if len(price_history) < 2:
            return 0
        
        prices = np.array(list(price_history))
        momentum = 0
        for i in range(1, len(prices)):
            weight = self.momentum_decay ** (len(prices) - i)
            momentum += weight * (prices[i] - prices[i-1])
        return momentum
    
    def _calculate_volatility(self, price_history):
        """Calculate price volatility as coefficient of variation."""
        if len(price_history) < 2:
            return 0
        prices = np.array(list(price_history))
        mean_price = np.mean(prices)
        if mean_price == 0:
            return 0
        volatility = (np.std(prices) / mean_price) * 100
        return volatility
    
    def _calculate_discharge_value(self, current_buy_price, current_sell_price, 
                                   sell_price_history, soc):
        """Calculate value proposition of discharging now vs. waiting."""
        if current_sell_price <= 0:
            return 0
        
        spread = (current_buy_price - current_sell_price) / current_sell_price
        
        sell_momentum = self._calculate_price_momentum(sell_price_history)
        momentum_bonus = (sell_momentum / max(abs(sell_momentum), 0.01)) * 0.1
        
        soc_penalty = max(0, (soc - 0.70) * 0.2)
        
        total_value = spread + momentum_bonus - soc_penalty
        return total_value
    
    def _calculate_charge_value(self, current_buy_price, current_sell_price,
                                buy_price_history, soc):
        """Calculate value proposition of charging now vs. waiting."""
        if current_buy_price <= 0:
            return 0
        
        spread = -(current_buy_price - current_sell_price) / current_buy_price
        
        buy_momentum = self._calculate_price_momentum(buy_price_history)
        momentum_penalty = (buy_momentum / max(abs(buy_momentum), 0.01)) * 0.1
        
        soc_bonus = max(0, (0.30 - soc) * 0.15)
        
        total_value = spread - momentum_penalty + soc_bonus
        return total_value
    
    def take_action(self,
                    current_energy_stored_kwh: float,
                    current_pv_generation_kw: float,
                    current_demand_kw: float,
                    current_grid_buy_price: float,
                    current_grid_sell_price: float,
                    battery_capacity_kwh: float) -> float:
        """Determines target battery power using predictive price-responsive logic."""
        
        self.buy_price_history.append(current_grid_buy_price)
        self.sell_price_history.append(current_grid_sell_price)
        
        soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
        net_generation = current_pv_generation_kw - current_demand_kw
        
        buy_percentile = self._calculate_price_percentile(current_grid_buy_price, 
                                                          self.buy_price_history)
        sell_percentile = self._calculate_price_percentile(current_grid_sell_price, 
                                                           self.sell_price_history)
        volatility = self._calculate_volatility(self.buy_price_history)
        
        action_kw = 0.0
        
        if net_generation < 0 or sell_percentile > self.price_percentile_discharge:
            discharge_value = self._calculate_discharge_value(
                current_grid_buy_price, current_grid_sell_price,
                self.sell_price_history, soc
            )
            
            can_discharge = (
                (net_generation < 0 and soc > self.min_soc_hard_limit) or
                (discharge_value > 0.10 and soc > self.min_soc_hard_limit)
            )
            
            if can_discharge:
                deficit = abs(min(net_generation, 0))
                max_available_discharge = current_energy_stored_kwh / (1/60)
                discharge_power = min(
                    deficit if deficit > 0 else self.max_discharge_power * 0.5,
                    self.max_discharge_power,
                    max_available_discharge
                )
                action_kw = -discharge_power
        
        if action_kw == 0:
            can_charge_from_pv = net_generation > 0 and soc < self.max_soc_hard_limit
            can_charge_from_grid = (
                buy_percentile < self.price_percentile_charge and
                soc < self.max_soc_hard_limit and
                volatility > self.min_volatility_threshold
            )
            
            if can_charge_from_pv:
                max_available_charge = (battery_capacity_kwh - current_energy_stored_kwh) / (1/60)
                charge_power = min(
                    net_generation,
                    self.max_charge_power,
                    max_available_charge
                )
                action_kw = charge_power
            
            elif can_charge_from_grid:
                charge_value = self._calculate_charge_value(
                    current_grid_buy_price, current_grid_sell_price,
                    self.buy_price_history, soc
                )
                
                if charge_value > 0.05:
                    max_available_charge = (battery_capacity_kwh - current_energy_stored_kwh) / (1/60)
                    charge_power = min(
                        self.max_charge_power * 0.7,
                        max_available_charge
                    )
                    action_kw = charge_power
        
        return action_kw
