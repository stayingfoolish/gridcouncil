class Policy:
  def __init__(self):
    self.buy_price_ma = 100.0
    self.sell_price_ma = 100.0
    self.price_volatility = 5.0
    self.recent_prices_buy = [100.0] * 10
    self.recent_prices_sell = [100.0] * 10
    self.momentum_score = 0.0
    self.target_soc = 0.50

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    # === REAL-TIME VOLATILITY CALCULATION ===
    self.recent_prices_buy.pop(0)
    self.recent_prices_buy.append(current_grid_buy_price)
    self.recent_prices_sell.pop(0)
    self.recent_prices_sell.append(current_grid_sell_price)

    buy_prices_array = self.recent_prices_buy
    sell_prices_array = self.recent_prices_sell
    buy_mean = sum(buy_prices_array) / len(buy_prices_array)
    sell_mean = sum(sell_prices_array) / len(sell_prices_array)

    buy_variance = sum((p - buy_mean) ** 2 for p in buy_prices_array) / len(buy_prices_array)
    self.price_volatility = (buy_variance ** 0.5) / max(buy_mean, 1.0)

    # Update moving averages with volatility-adaptive weighting
    vol_weight = min(0.3 + self.price_volatility * 0.5, 0.8)  # Higher vol = more responsive
    self.buy_price_ma = self.buy_price_ma * (1 - vol_weight) + current_grid_buy_price * vol_weight
    self.sell_price_ma = self.sell_price_ma * (1 - vol_weight) + current_grid_sell_price * vol_weight

    # Momentum score: (current - MA) / MA (raw price deviation)
    self.momentum_score = ((current_grid_buy_price - self.buy_price_ma) +
                          (self.sell_price_ma - current_grid_sell_price)) / max(self.buy_price_ma, 1.0)

    battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    pv_surplus = current_pv_generation_kw - current_demand_kw
    margin = current_grid_sell_price - current_grid_buy_price

    # === DYNAMIC TARGET SOC ===
    # Higher margin (better arbitrage conditions) → prefer lower SOC (ready to buy)
    # Lower margin → prefer higher SOC (hold for better sell)
    margin_normalized = margin / max(self.sell_price_ma, 1.0)
    self.target_soc = 0.50 + (0.25 * (0.05 - margin_normalized))  # Range [0.25, 0.75]
    self.target_soc = max(0.25, min(self.target_soc, 0.75))

    # === CRITICAL RESERVES (Override all other logic) ===
    if battery_soc < 0.10:
      return 12.0  # Emergency charge
    if battery_soc > 0.95:
      return -10.0  # Emergency discharge

    # === PV SYNCHRONIZATION (Highest Priority) ===
    if pv_surplus > 1.5 and battery_soc < 0.85:
      return min(pv_surplus * 0.85, 10.0)
    if pv_surplus < -1.5 and battery_soc > 0.15:
      return max(pv_surplus * 0.85, -8.0)

    # === UNIFIED ARBITRAGE LOGIC ===
    # Threshold adapts inversely to volatility (high vol → lower threshold = more active)
    base_threshold = 0.03  # Start at 3% instead of 8%
    threshold_adjustment = -0.01 * self.price_volatility  # Reduce by 1% per unit volatility
    effective_threshold = max(base_threshold + threshold_adjustment, 0.01)

    buy_discount_pct = (self.buy_price_ma - current_grid_buy_price) / max(self.buy_price_ma, 1.0)
    sell_premium_pct = (current_grid_sell_price - self.sell_price_ma) / max(self.sell_price_ma, 1.0)

    # CHARGING: Pull SOC toward target when opportunity exists
    if buy_discount_pct > effective_threshold and battery_soc < self.target_soc:
      # Scale action: discount magnitude × distance to target
      charge_intensity = min(buy_discount_pct / 0.05, 1.0)  # 0-1 normalized
      soc_gap = (self.target_soc - battery_soc) / max(self.target_soc, 0.5)
      action_kw = 2.0 + (charge_intensity * 10.0) * soc_gap
      return min(action_kw, 12.0)

    # DISCHARGING: Pull SOC toward target when opportunity exists
    elif sell_premium_pct > effective_threshold and battery_soc > self.target_soc:
      # Scale action: premium magnitude × distance to target
      discharge_intensity = min(sell_premium_pct / 0.05, 1.0)  # 0-1 normalized
      soc_gap = (battery_soc - self.target_soc) / max(1.0 - self.target_soc, 0.5)
      action_kw = -2.0 - (discharge_intensity * 8.0) * soc_gap
      return max(action_kw, -10.0)

    # REBALANCING: Drift toward target SOC even without strong arbitrage signal
    if abs(battery_soc - self.target_soc) > 0.15:
      if battery_soc < self.target_soc:
        return 3.0  # Gentle charge
      else:
        return -2.5  # Gentle discharge

    return 0.0
