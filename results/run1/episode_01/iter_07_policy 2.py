class Policy:
  def __init__(self):
    """Aggressive arbitrage-focused battery management"""
    self.price_history = []
    self.spread_history = []
    self.max_history = 120

    # Arbitrage detection parameters
    self.minimum_spread_threshold = 0.15  # Trigger at 15¢ spread
    self.spread_acceleration_window = 10  # Recent spread trend
    self.profitable_spread_ma = 20  # Historical profitable spread baseline

    # Aggressive utilization targets
    self.max_soc_for_charging = 0.75  # Charge up to 75% when spreads widen
    self.min_soc_for_discharging = 0.25  # Discharge down to 25% when selling

    # Commitment tracking
    self.arbitrage_confidence = 0.0
    self.current_spread = 0.0
    self.spread_trend = 0.0

  def calculate_price_spread(self, buy_price, sell_price):
    """Track profit margin opportunity"""
    return sell_price - buy_price

  def detect_arbitrage_regime(self, buy_price, sell_price):
    """Aggressive arbitrage detection instead of market regime"""
    if len(self.price_history) < self.spread_acceleration_window:
      return "calibrating", 0.0

    current_spread = self.calculate_price_spread(buy_price, sell_price)
    self.spread_history.append(current_spread)
    if len(self.spread_history) > self.max_history:
      self.spread_history.pop(0)

    self.current_spread = current_spread

    # Recent spread trend (acceleration)
    recent_spreads = self.spread_history[-self.spread_acceleration_window:]
    spread_trend = recent_spreads[-1] - recent_spreads[0]
    self.spread_trend = spread_trend

    # Average profitable spread
    if len(self.spread_history) >= self.profitable_spread_ma:
      avg_profitable = sum(self.spread_history[-self.profitable_spread_ma:]) / self.profitable_spread_ma
    else:
      avg_profitable = 0.1

    # Arbitrage confidence: based on spread magnitude and trend
    spread_vs_baseline = current_spread - avg_profitable

    # Negative spread = losing money; positive = making money
    if current_spread < -0.05:  # Prices inverted (should not charge)
      return "sell_opportunity", 0.9  # Strong sell signal
    elif current_spread > self.minimum_spread_threshold and spread_trend > 0:
      # Widening spread = favorable arbitrage window opening
      confidence = min(0.95, current_spread * 2.0)
      return "buy_for_arbitrage", confidence
    elif current_spread > 0.05 and spread_trend < 0:
      # Spread narrowing but still positive = urgent to sell
      confidence = min(0.95, (current_spread + abs(spread_trend)) * 1.5)
      return "sell_signal", confidence
    else:
      return "hold", 0.3

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Execute aggressive arbitrage strategy"""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Detect arbitrage opportunity
    arbitrage_regime, confidence = self.detect_arbitrage_regime(
      current_grid_buy_price,
      current_grid_sell_price
    )

    action_kw = 0.0
    max_rate = battery_capacity_kwh * 0.50  # Higher power rates (50% instead of 40%)

    # **AGGRESSIVE MODE**: Commit heavily to profitable spreads
    if arbitrage_regime == "buy_for_arbitrage" and confidence > 0.6:
      # When spread is widening favorably, charge hard
      charge_power = min(confidence * max_rate, max_rate)
      # But respect physical limits
      max_charge = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(charge_power, max_charge)

    elif arbitrage_regime == "sell_signal" and confidence > 0.6:
      # When we have energy and spread is positive, discharge aggressively
      discharge_power = min(confidence * max_rate, max_rate)
      max_discharge = current_energy_stored_kwh
      action_kw = max(-discharge_power, -max_discharge)

    elif arbitrage_regime == "sell_opportunity":
      # Inversion detected—sell everything at any price
      action_kw = -current_energy_stored_kwh

    else:
      # In hold mode: use renewable+demand matching
      net_power = current_pv_generation_kw - current_demand_kw

      # Opportunistic charging: if we have excess PV and good spread ahead
      if net_power > 0.5 and self.current_spread > 0.02 and current_soc < 0.7:
        action_kw = min(net_power * 0.8, 8.0)
      # Opportunistic discharging: if we need power and can sell above cost
      elif net_power < -0.5 and self.current_spread > 0.01 and current_soc > 0.3:
        action_kw = max(net_power * 0.8, -8.0)

    return action_kw
