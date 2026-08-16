class Policy:
  def __init__(self):
    self.buy_price_ma = 100.0
    self.sell_price_ma = 100.0
    self.margin_ma = 0.0  # Track average margin for context
    self.consecutive_charge_steps = 0
    self.consecutive_discharge_steps = 0
    self.locked_in_charge = False
    self.locked_in_discharge = False

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    # Update moving averages (aggressive: 0.7 weight on new data)
    self.buy_price_ma = self.buy_price_ma * 0.3 + current_grid_buy_price * 0.7
    self.sell_price_ma = self.sell_price_ma * 0.3 + current_grid_sell_price * 0.7
    current_margin = current_grid_sell_price - current_grid_buy_price
    self.margin_ma = self.margin_ma * 0.4 + current_margin * 0.6

    # State calculation
    battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    pv_surplus = current_pv_generation_kw - current_demand_kw

    # Price deviation from baselines (normalized)
    buy_discount = (self.buy_price_ma - current_grid_buy_price) / max(self.buy_price_ma, 1.0)
    sell_premium = (current_grid_sell_price - self.sell_price_ma) / max(self.sell_price_ma, 1.0)

    # Margin metrics
    margin_vs_average = (current_margin - self.margin_ma) / max(abs(self.margin_ma), 1.0)
    normalized_margin = current_margin / max(self.sell_price_ma, 1.0)  # As % of typical sell price

    # ===== ZONE 1: Forced Charging (Below critical SOC) =====
    if battery_soc < 0.15:
      if current_grid_buy_price < self.buy_price_ma * 1.1:  # Willing to pay up to 10% premium
        return 12.0  # Max charge to recover
      else:
        return 6.0  # Still charge even at premium

    # ===== ZONE 2: Forced Discharge (Above critical SOC) =====
    if battery_soc > 0.95:
      if current_grid_sell_price > self.sell_price_ma * 0.9:  # Take reasonable sell prices
        return -10.0  # Reduce excess
      else:
        return -5.0

    # ===== ZONE 3: PV Responsiveness (Natural generation) =====
    if pv_surplus > 2.0 and battery_soc < 0.85:
      # Direct PV charging: strong signal, minimal arbitrage consideration
      action_kw = min(pv_surplus * 0.8, 10.0)
      return action_kw

    if pv_surplus < -2.0 and battery_soc > 0.20:
      # PV deficit: use battery to cover demand
      action_kw = max(pv_surplus * 0.8, -8.0)
      return action_kw

    # ===== ZONE 4: Arbitrage-Driven Control (Margin opportunity) =====
    # Only engage arbitrage between 25%-75% SOC (operational sweet spot)
    if 0.25 <= battery_soc <= 0.75:

      # **CHARGING ARBITRAGE**: Buy low (or very low relative to baseline)
      if buy_discount > 0.08:  # Buy price >8% below baseline = strong opportunity
        # Scale charge rate by: (1) margin size, (2) distance from full
        available_capacity_ratio = (0.85 - battery_soc) / 0.6
        charge_intensity = min(buy_discount * 80.0, 1.0)  # 0-1 normalized
        action_kw = 4.0 + (charge_intensity * 8.0) * available_capacity_ratio

        # Commit to charging momentum (reduce hysteresis)
        self.consecutive_charge_steps += 1
        if self.consecutive_charge_steps > 2:
          self.locked_in_charge = True

        if self.locked_in_charge:
          action_kw = min(action_kw + 2.0, 14.0)  # Boost during committed phase

        return min(action_kw, 14.0)

      # **DISCHARGING ARBITRAGE**: Sell high (or very high relative to baseline)
      elif sell_premium > 0.12:  # Sell price >12% above baseline = strong opportunity
        # Scale discharge rate by: (1) margin size, (2) distance from empty
        discharge_intensity = min(sell_premium * 60.0, 1.0)  # 0-1 normalized
        available_energy_ratio = (battery_soc - 0.15) / 0.6
        action_kw = -3.0 - (discharge_intensity * 7.0) * available_energy_ratio

        # Commit to discharge momentum
        self.consecutive_discharge_steps += 1
        if self.consecutive_discharge_steps > 2:
          self.locked_in_discharge = True

        if self.locked_in_discharge:
          action_kw = max(action_kw - 2.0, -12.0)  # Boost during committed phase

        return max(action_kw, -12.0)

      else:
        # No strong arbitrage signal: reset momentum trackers
        self.consecutive_charge_steps = 0
        self.consecutive_discharge_steps = 0
        self.locked_in_charge = False
        self.locked_in_discharge = False
        return 0.0

    # Default: no action
    return 0.0
