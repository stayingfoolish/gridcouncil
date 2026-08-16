import numpy as np
from collections import defaultdict

class DispatchPolicy:
    def __init__(self):
        """Deadline-aware cost minimization with forward-looking dispatch."""
        self.price_history = []
        self.hour_price_stats = defaultdict(lambda: {'prices': [], 'mean': 0, 'std': 0})
        self.max_history = 168
        self.iteration_count = 0
        
        self.learned_hod_profile = np.ones(24) * 50.0
        self.hod_learning_rate = 0.15
        
        self.backlog_cohorts = []
        self.last_battery_action = 0.0
        
    def update_hod_learning(self, hour_of_day: int, current_price: float):
        """Learn diurnal price pattern."""
        self.hour_price_stats[hour_of_day]['prices'].append(current_price)
        prices = self.hour_price_stats[hour_of_day]['prices']
        
        if len(prices) > 24:
            prices.pop(0)
        
        mean_price = np.mean(prices)
        self.learned_hod_profile[hour_of_day] = (
            (1 - self.hod_learning_rate) * self.learned_hod_profile[hour_of_day] +
            self.hod_learning_rate * mean_price
        )
    
    def estimate_forward_price(self, hours_ahead: int, current_price: float,
                              hour_of_day: int, volatility: float) -> float:
        """Estimate expected price N hours ahead using diurnal pattern + volatility."""
        future_hod = (hour_of_day + hours_ahead) % 24
        
        base_forward = self.learned_hod_profile[future_hod]
        regime_adjustment = current_price / (np.mean(self.learned_hod_profile) + 1e-6)
        
        forward_estimate = base_forward * regime_adjustment
        
        uncertainty_factor = 1.0 + (volatility * 0.1 * np.sqrt(hours_ahead / 24.0))
        
        return forward_estimate * uncertainty_factor
    
    def compute_backlog_cohorts(self, backlog_mwh: float, 
                               oldest_backlog_age_h: float) -> list:
        """Decompose backlog into age-based cohorts with deadline urgency."""
        if backlog_mwh < 1e-6:
            return []
        
        age_brackets = [
            (0, 8, oldest_backlog_age_h * 0.3),
            (8, 16, oldest_backlog_age_h * 0.5),
            (16, 24, oldest_backlog_age_h * 0.2),
        ]
        
        cohorts = []
        
        for idx, (age_min, age_max, age_rep) in enumerate(age_brackets):
            fraction = [0.3, 0.5, 0.2][idx]
            cohort_mwh = backlog_mwh * fraction
            deadline_remaining = max(1.0, 24.0 - age_rep)
            
            cohorts.append({
                'mwh': cohort_mwh,
                'age_hours': age_rep,
                'deadline_hours': deadline_remaining,
                'urgency': 1.0 / max(0.1, deadline_remaining),
                'cohort_id': idx
            })
        
        return cohorts
    
    def compute_serve_cost(self, mwh: float, current_price: float,
                          hours_to_deadline: float) -> float:
        """Cost of serving 1 MWh now vs. marginal cost of deferral."""
        immediate_cost = mwh * current_price
        deferral_risk = mwh * 50.0 * (1.0 / max(0.5, hours_to_deadline))
        
        return immediate_cost + deferral_risk
    
    def take_action(self,
                    hour_of_day: int,
                    current_price: float,
                    firm_load_mw: float,
                    arriving_flex_mw: float,
                    backlog_mwh: float,
                    oldest_backlog_age_h: float,
                    battery_soc_mwh: float,
                    battery_capacity_mwh: float,
                    battery_power_mw: float) -> tuple:
        
        self.iteration_count += 1
        self.price_history.append(current_price)
        if len(self.price_history) > self.max_history:
            self.price_history.pop(0)
        
        self.update_hod_learning(hour_of_day, current_price)
        
        if len(self.price_history) >= 24:
            sorted_prices = sorted(self.price_history)
            p10 = sorted_prices[len(sorted_prices) // 10]
            p25 = sorted_prices[len(sorted_prices) // 4]
            p50 = sorted_prices[len(sorted_prices) // 2]
            p75 = sorted_prices[3 * len(sorted_prices) // 4]
            p90 = sorted_prices[9 * len(sorted_prices) // 10]
            
            price_spread = p90 - p10
            volatility = price_spread / max(1.0, p50)
        else:
            p10 = p25 = p50 = p75 = p90 = current_price
            volatility = 0.3
        
        price_1h_ahead = self.estimate_forward_price(1, current_price, hour_of_day, volatility)
        price_4h_ahead = self.estimate_forward_price(4, current_price, hour_of_day, volatility)
        price_12h_ahead = self.estimate_forward_price(12, current_price, hour_of_day, volatility)
        
        price_trend = (price_4h_ahead - current_price) / (current_price + 1e-6)
        price_trend = np.clip(price_trend, -0.15, 0.15)
        
        flex_serve_mw = 0.0
        
        if backlog_mwh > 1e-6:
            cohorts = self.compute_backlog_cohorts(backlog_mwh, oldest_backlog_age_h)
            
            for cohort in cohorts:
                serve_now_cost = self.compute_serve_cost(cohort['mwh'], current_price, 
                                                         cohort['deadline_hours'])
                defer_expected_cost = self.compute_serve_cost(cohort['mwh'], price_4h_ahead,
                                                              cohort['deadline_hours'] - 4)
                
                cost_advantage = (defer_expected_cost - serve_now_cost) / (serve_now_cost + 1e-6)
                
                should_serve_urgency = cohort['deadline_hours'] < 4.0
                should_serve_economics = cost_advantage > 0.05
                should_serve_price_spike = current_price > p90
                
                if should_serve_urgency:
                    serve_fraction = 1.0
                elif should_serve_economics and not should_serve_price_spike:
                    serve_fraction = 0.8
                elif price_trend < -0.1 and battery_soc_mwh > battery_capacity_mwh * 0.40:
                    serve_fraction = 0.5
                elif should_serve_price_spike:
                    serve_fraction = 0.2
                else:
                    serve_fraction = 0.4
                
                flex_serve_mw += cohort['mwh'] / max(1.0, cohort['deadline_hours']) * serve_fraction
        
        flex_serve_mw = min(flex_serve_mw, arriving_flex_mw * 1.3)
        flex_serve_mw = max(0.0, flex_serve_mw)
        
        battery_mw = 0.0
        
        estimated_peak_next_4h = max(current_price, price_4h_ahead)
        battery_discharge_value = estimated_peak_next_4h - p50
        battery_charge_cost = current_price
        
        soc_ratio = battery_soc_mwh / (battery_capacity_mwh + 1e-6)
        
        if current_price < p25 and battery_discharge_value > 20.0:
            available_charge = min(
                battery_power_mw,
                (battery_capacity_mwh - battery_soc_mwh) / 1.0,
                battery_power_mw * min(1.5, battery_discharge_value / 50.0)
            )
            battery_mw = available_charge
        
        elif current_price > p75 and soc_ratio > 0.12:
            min_soc = battery_capacity_mwh * 0.08
            available_discharge = min(
                battery_power_mw,
                (battery_soc_mwh - min_soc) / 1.0,
                battery_power_mw * min(1.5, (current_price - p50) / 50.0)
            )
            battery_mw = -available_discharge
        
        elif price_trend > 0.15 and soc_ratio > 0.25:
            available_discharge = min(
                battery_power_mw * 0.6,
                (battery_soc_mwh - battery_capacity_mwh * 0.15) / 1.0
            )
            battery_mw = -available_discharge
        
        elif price_trend < -0.12 and soc_ratio < 0.50:
            available_charge = min(
                battery_power_mw * 0.7,
                (battery_capacity_mwh - battery_soc_mwh) / 1.0
            )
            battery_mw = available_charge
        
        if abs(battery_mw - self.last_battery_action) > battery_power_mw * 0.8:
            battery_mw = self.last_battery_action + np.sign(battery_mw - self.last_battery_action) * battery_power_mw * 0.4
        
        self.last_battery_action = battery_mw
        
        return flex_serve_mw, battery_mw
