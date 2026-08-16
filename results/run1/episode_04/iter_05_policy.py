import math

class Policy:
    def __init__(self):
        """Regime-based arbitrage with hysteresis and multi-horizon forecasting."""
        self.price_history = []
        self.max_history_length = 168  # Full week for better pattern discovery

        # Multi-horizon price statistics
        self.hourly_stats = {'mean': 0.0, 'std': 0.0}
        self.daily_stats = {'mean': 0.0, 'std': 0.0}
        self.weekly_stats = {'mean': 0.0, 'std': 0.0}

        # Regime state machine
        self.current_regime = 'NEUTRAL'
        self.regime_duration = 0  # Hours in current regime
        self.regime_hysteresis_threshold = 0.08  # 8% price differential to flip regime

        # Per-regime action profiles (locked-in behavior)
        self.regime_actions = {
            'CHARGE': {'min_rate': 10.0, 'max_rate': 14.0, 'soc_max': 0.90},
            'HOLD': {'rate': 0.0, 'soc_band': (0.40, 0.60)},
            'RELEASE': {'min_rate': 6.0, 'max_rate': 9.5, 'soc_min': 0.20},
            'NEUTRAL': {'min_rate': 0.0, 'max_rate': 0.0, 'soc_min': 0.30, 'soc_max': 0.70}
        }

        # Track regime stability
        self.regime_entry_metric = 0.0  # Price metric when regime started
        self.consecutive_regime_violations = 0

        # Action history for regret minimization
        self.action_regrets = []  # Track decisions that went wrong
        self.max_regret_history = 30

    def _update_price_statistics(self, prices):
        """Compute hourly, daily, and weekly price statistics."""
        if len(prices) < 2:
            return

        # Hourly: last 24 hours
        hourly = prices[-24:] if len(prices) >= 24 else prices
        self.hourly_stats = self._compute_stats(hourly)

        # Daily: last 7 days (168 hours)
        daily = prices[-168:] if len(prices) >= 168 else prices
        self.daily_stats = self._compute_stats(daily)

        # Weekly: all available (up to 168)
        self.weekly_stats = self._compute_stats(prices[-168:])

    def _compute_stats(self, prices):
        """Calculate mean and standard deviation."""
        if not prices:
            return {'mean': 0.0, 'std': 0.0}
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        return {'mean': mean, 'std': math.sqrt(variance)}

    def _determine_regime(self, current_price, battery_level_ratio):
        """
        Determine next regime based on multi-horizon price signals.
        Implements hysteresis to prevent oscillation.
        """
        if len(self.price_history) < 24:
            return 'NEUTRAL'

        # Compute price percentile relative to weekly baseline
        weekly_mean = self.weekly_stats['mean']
        weekly_std = self.weekly_stats['std']

        if weekly_std == 0:
            return self.current_regime

        z_score = (current_price - weekly_mean) / weekly_std

        # Hysteresis logic: regime changes only with sufficient price swing
        regime_change_threshold = self.regime_hysteresis_threshold

        # Proposed regimes based on z-score and battery level
        if z_score < -0.6 and battery_level_ratio < 0.80:
            # Prices very low → charge aggressively
            proposed_regime = 'CHARGE'
        elif z_score > 0.6 and battery_level_ratio > 0.20:
            # Prices very high → discharge aggressively
            proposed_regime = 'RELEASE'
        elif -0.2 <= z_score <= 0.2 and 0.35 < battery_level_ratio < 0.65:
            # Prices near average and battery in comfortable range → hold
            proposed_regime = 'HOLD'
        else:
            # Ambiguous conditions
            proposed_regime = 'NEUTRAL'

        # Apply hysteresis: only switch if price swing is sufficient
        if proposed_regime != self.current_regime:
            price_change_ratio = abs(current_price - self.regime_entry_metric) / max(self.regime_entry_metric, 0.01)
            if price_change_ratio > regime_change_threshold:
                return proposed_regime
            else:
                return self.current_regime

        return proposed_regime

    def _execute_regime_action(self, regime, battery_level_ratio, available_capacity, current_energy_stored, pv_generation, demand):
        """
        Execute locked-in action profile for current regime.
        """
        action_kw = 0.0

        if regime == 'CHARGE':
            # Aggressive charging within constraints
            if battery_level_ratio < self.regime_actions['CHARGE']['soc_max']:
                # Prioritize solar integration + grid purchase at low prices
                available_cap = available_capacity
                # Base charge rate + adaptive boost based on available capacity
                action_kw = min(
                    self.regime_actions['CHARGE']['max_rate'],
                    self.regime_actions['CHARGE']['min_rate'] + available_cap * 0.02
                )

        elif regime == 'RELEASE':
            # Aggressive discharging within constraints
            if battery_level_ratio > self.regime_actions['RELEASE']['soc_min']:
                available_discharge = current_energy_stored
                # Base discharge rate + adaptive scaling
                action_kw = -min(
                    self.regime_actions['RELEASE']['max_rate'],
                    self.regime_actions['RELEASE']['min_rate'] + available_discharge * 0.015
                )

        elif regime == 'HOLD':
            # Maintain mid-range SOC; avoid trading
            if battery_level_ratio < self.regime_actions['HOLD']['soc_band'][0]:
                # Gentle charge to reach target
                action_kw = 3.0
            elif battery_level_ratio > self.regime_actions['HOLD']['soc_band'][1]:
                # Gentle discharge to reach target
                action_kw = -2.5
            else:
                action_kw = 0.0

        else:  # NEUTRAL
            # Minimal trading; maintain broad range
            if battery_level_ratio < self.regime_actions['NEUTRAL']['soc_min']:
                action_kw = 2.0
            elif battery_level_ratio > self.regime_actions['NEUTRAL']['soc_max']:
                action_kw = -1.5
            else:
                action_kw = 0.0

        return action_kw

    def _record_regret(self, action_taken, current_price, next_price_estimate):
        """
        Track actions that went wrong (negative regret).
        Use to inform future regime decisions.
        """
        if action_taken > 0.5:  # We charged
            regret = (next_price_estimate - current_price) * action_taken  # Bad if price drops
        elif action_taken < -0.5:  # We discharged
            regret = (current_price - next_price_estimate) * abs(action_taken)  # Bad if price rises
        else:
            regret = 0.0

        if len(self.action_regrets) >= self.max_regret_history:
            self.action_regrets.pop(0)

        self.action_regrets.append(regret)

    def take_action(self,
        current_energy_stored_kwh: float,
        current_pv_generation_kw: float,
        current_demand_kw: float,
        current_grid_buy_price: float,
        current_grid_sell_price: float,
        battery_capacity_kwh: float,
    ) -> float:
        """Execute regime-based policy with hysteresis."""

        self.price_history.append(current_grid_buy_price)
        if len(self.price_history) > self.max_history_length:
            self.price_history.pop(0)

        battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
        available_capacity = battery_capacity_kwh - current_energy_stored_kwh

        # Update multi-horizon statistics
        self._update_price_statistics(self.price_history)

        # Determine regime (with hysteresis protection)
        proposed_regime = self._determine_regime(current_grid_buy_price, battery_level_ratio)

        if proposed_regime != self.current_regime:
            self.regime_duration = 0
            self.regime_entry_metric = current_grid_buy_price
            self.current_regime = proposed_regime
        else:
            self.regime_duration += 1

        # Execute locked regime action
        action_kw = self._execute_regime_action(
            self.current_regime,
            battery_level_ratio,
            available_capacity,
            current_energy_stored_kwh,
            current_pv_generation_kw,
            current_demand_kw
        )

        # Record regret for learning (future enhancement)
        estimated_next_price = current_grid_buy_price * 1.02  # Simple placeholder
        self._record_regret(action_kw, current_grid_buy_price, estimated_next_price)

        return action_kw
