import math

class Policy:
    def __init__(self):
        """Regret-minimizing predictive control for battery arbitrage."""
        self.price_history = []
        self.max_history_length = 168

        # Adaptive threshold parameters (learned from regret)
        self.charge_threshold = -0.5  # Z-score below which to charge
        self.discharge_threshold = 0.5  # Z-score above which to discharge
        self.threshold_learning_rate = 0.02

        # Price statistics
        self.hourly_stats = {'mean': 0.0, 'std': 0.0}
        self.daily_stats = {'mean': 0.0, 'std': 0.0}
        self.weekly_stats = {'mean': 0.0, 'std': 0.0}

        # Price trend tracking (velocity + acceleration)
        self.price_velocity = 0.0  # (price_t - price_t-1)
        self.price_acceleration = 0.0  # (velocity_t - velocity_t-1)
        self.trend_weight = 0.3  # How much to weight trend vs level

        # Regret accumulator for threshold adaptation
        self.cumulative_regret = 0.0
        self.regret_window = []
        self.max_regret_window = 24

        # Per-action opportunity tracking
        self.charge_opportunities = []
        self.discharge_opportunities = []
        self.max_opportunity_history = 50

    def _update_price_statistics(self, prices):
        """Compute multi-horizon statistics with trend data."""
        if len(prices) < 2:
            return

        # Compute trend velocity and acceleration
        if len(prices) >= 2:
            new_velocity = prices[-1] - prices[-2]
            self.price_acceleration = new_velocity - self.price_velocity
            self.price_velocity = new_velocity

        # Hourly: last 24 hours
        hourly = prices[-24:] if len(prices) >= 24 else prices
        self.hourly_stats = self._compute_stats(hourly)

        # Daily: last 7 days
        daily = prices[-168:] if len(prices) >= 168 else prices
        self.daily_stats = self._compute_stats(daily)

        # Weekly: all available
        self.weekly_stats = self._compute_stats(prices[-168:])

    def _compute_stats(self, prices):
        """Calculate mean and standard deviation."""
        if not prices:
            return {'mean': 0.0, 'std': 0.0}
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        return {'mean': mean, 'std': math.sqrt(variance)}

    def _compute_price_signal(self, current_price):
        """
        Compute composite price signal combining:
        - Z-score (relative to weekly baseline)
        - Price momentum (short-term trend)
        - Price acceleration (second-order signal)
        """
        if len(self.price_history) < 24:
            return 0.0

        weekly_mean = self.weekly_stats['mean']
        weekly_std = self.weekly_stats['std']

        if weekly_std == 0:
            return 0.0

        # Base z-score
        z_score = (current_price - weekly_mean) / weekly_std

        # Normalize velocity to z-score units
        velocity_signal = self.price_velocity / max(weekly_std, 0.01)

        # Acceleration signal (detects trend changes)
        accel_signal = self.price_acceleration / max(weekly_std, 0.01)

        # Composite: weighted combination
        # When price is low AND falling (velocity negative) -> stronger charge signal
        # When price is high AND rising (velocity positive) -> stronger discharge signal
        composite = z_score + (self.trend_weight * velocity_signal) + (0.1 * accel_signal)

        return composite

    def _adapt_thresholds(self):
        """
        Learn from regret: if we charged when price fell (bad), raise charge_threshold
        If we discharged when price rose (bad), lower discharge_threshold
        This makes the algorithm MORE conservative about trades that hurt us.
        """
        if len(self.regret_window) == 0:
            return

        recent_regret = sum(self.regret_window) / len(self.regret_window)

        # If recent regret is negative (we've been losing money), become more conservative
        if recent_regret < -0.5:
            # Raise charge threshold (harder to trigger charging)
            self.charge_threshold += self.threshold_learning_rate * 0.1
            # Lower discharge threshold (harder to trigger discharging)
            self.discharge_threshold -= self.threshold_learning_rate * 0.1

        # Clamp thresholds to reasonable bounds
        self.charge_threshold = max(-1.5, min(-0.2, self.charge_threshold))
        self.discharge_threshold = max(0.2, min(1.5, self.discharge_threshold))

    def _compute_continuous_action(self, price_signal, battery_level_ratio):
        """
        Convert price signal and battery state to continuous charging rate.

        Action scale:
        - Positive: charging (0 to +14 kW)
        - Negative: discharging (-10 to 0 kW)
        - Zero: holding
        """
        action_kw = 0.0

        # CHARGING LOGIC
        if price_signal < self.charge_threshold and battery_level_ratio < 0.85:
            # Price is attractively low; charge proportional to opportunity strength
            opportunity_strength = min(1.0, abs(price_signal - self.charge_threshold) / 1.0)

            # Tapered by how close battery is to full
            battery_headroom = 1.0 - battery_level_ratio
            headroom_factor = min(1.0, battery_headroom / 0.3)  # Full impact until 70% SOC

            action_kw = 14.0 * opportunity_strength * headroom_factor
            self.charge_opportunities.append(opportunity_strength)

        # DISCHARGING LOGIC
        elif price_signal > self.discharge_threshold and battery_level_ratio > 0.15:
            # Price is attractively high; discharge proportional to opportunity strength
            opportunity_strength = min(1.0, abs(price_signal - self.discharge_threshold) / 1.0)

            # Tapered by how close battery is to empty
            battery_margin = battery_level_ratio
            margin_factor = min(1.0, battery_margin / 0.3)  # Full impact until 30% SOC

            action_kw = -10.0 * opportunity_strength * margin_factor
            self.discharge_opportunities.append(opportunity_strength)

        else:
            # Price signal neutral or battery constraints prevent action
            # Only gentle balancing toward 50% SOC
            if battery_level_ratio < 0.45:
                action_kw = 1.5  # Slow charge to target
            elif battery_level_ratio > 0.55:
                action_kw = -1.0  # Slow discharge to target

        return action_kw

    def _record_regret(self, action_taken, current_price, next_price):
        """
        Calculate regret for this decision.
        Regret: opportunity cost of the action taken.
        """
        if action_taken > 0.5:  # Charged
            # Regret if price dropped after charging (we bought high)
            regret = (next_price - current_price) * action_taken
        elif action_taken < -0.5:  # Discharged
            # Regret if price rose after discharging (we sold low)
            regret = (current_price - next_price) * abs(action_taken)
        else:
            regret = 0.0

        # Track regret for recent window
        self.regret_window.append(regret)
        if len(self.regret_window) > self.max_regret_window:
            self.regret_window.pop(0)

        self.cumulative_regret += regret

        return regret

    def take_action(self,
        current_energy_stored_kwh: float,
        current_pv_generation_kw: float,
        current_demand_kw: float,
        current_grid_buy_price: float,
        current_grid_sell_price: float,
        battery_capacity_kwh: float,
    ) -> float:
        """Execute regret-minimizing predictive control."""

        # Update price history
        self.price_history.append(current_grid_buy_price)
        if len(self.price_history) > self.max_history_length:
            self.price_history.pop(0)

        battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

        # Update statistics and trends
        self._update_price_statistics(self.price_history)

        # Compute composite price signal
        price_signal = self._compute_price_signal(current_grid_buy_price)

        # Adapt thresholds based on recent regret
        self._adapt_thresholds()

        # Compute continuous action
        action_kw = self._compute_continuous_action(price_signal, battery_level_ratio)

        # Estimate next price using trend
        next_price_estimate = current_grid_buy_price + self.price_velocity * 1.5

        # Record regret for learning
        self._record_regret(action_kw, current_grid_buy_price, next_price_estimate)

        return action_kw
