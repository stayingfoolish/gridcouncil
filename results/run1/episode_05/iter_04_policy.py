class Policy:
  def __init__(self):
    self.price_history = []
    self.max_history_len = 20

  def get_price_stats(self):
    if len(self.price_history) < 2:
      return 0.1, 0.1  # Default std dev
    prices = self.price_history[-10:]
    avg = sum(prices) / len(prices)
    variance = sum((p - avg)**2 for p in prices) / len(prices)
    std = (variance ** 0.5) + 0.001
    return avg, std

  def calculate_arbitrage_signal(self, buy_price, sell_price, price_std):
    """Spread magnitude normalized by volatility."""
    spread = sell_price - buy_price
    normalized_spread = spread / (price_std + 0.001)
    return normalized_spread

  def calculate_soc_pressure(self, current_soc, capacity, volatility_factor=1.0):
    """Soft SOC bands with continuous pressure signal."""
    # Target range: 40-60% for normal conditions, 20-80% for high volatility
    target_low = 0.30 + (volatility_factor * 0.10)
    target_high = 0.70 - (volatility_factor * 0.10)
    soc_ratio = current_soc / capacity

    if soc_ratio < target_low:
      # Pressure to charge, increases as SoC drops
      pressure = -1.0 + ((soc_ratio - target_low) / (target_low + 0.001)) * 0.5
    elif soc_ratio > target_high:
      # Pressure to discharge, increases as SoC rises
      pressure = 1.0 + ((soc_ratio - target_high) / (1.0 - target_high)) * 0.5
    else:
      # Within target band, minimal pressure
      pressure = (soc_ratio - 0.5) * 0.3

    return pressure

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_len:
      self.price_history.pop(0)

    price_avg, price_std = self.get_price_stats()
    net_energy = current_pv_generation_kw - current_demand_kw

    # SIGNAL 1: Arbitrage signal (spread-driven)
    arbitrage = self.calculate_arbitrage_signal(
      current_grid_buy_price,
      current_grid_sell_price,
      price_std
    )

    # SIGNAL 2: SOC pressure (balance-driven)
    volatility = min(price_std / (price_avg + 0.001), 0.5)
    soc_pressure = self.calculate_soc_pressure(
      current_energy_stored_kwh,
      battery_capacity_kwh,
      volatility
    )

    # SIGNAL 3: PV pressure (generation-driven)
    pv_pressure = current_pv_generation_kw - current_demand_kw

    # COMBINED DECISION: Weighted sum of signals
    # Arbitrage takes priority, SOC keeps us balanced, PV handles surplus
    decision_signal = (
      arbitrage * 0.50 +      # Strong arbitrage signal
      soc_pressure * 0.30 +   # Maintain balance
      (pv_pressure / 10.0) * 0.20  # Normalize PV to similar scale
    )

    # POWER SCALING: Base power * signal magnitude, clamped by capacity
    base_charge_power = 8.0
    base_discharge_power = 7.0

    if decision_signal > 0:  # Charge
      available = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = (decision_signal ** 0.8) * base_charge_power  # Smooth curve
      action_kw = min(action_kw, available, base_charge_power * 1.2)
    else:  # Discharge
      available = current_energy_stored_kwh
      action_kw = -(abs(decision_signal) ** 0.8) * base_discharge_power  # Smooth curve
      action_kw = max(action_kw, -available, -base_discharge_power * 1.2)

    # SAFETY: Hard limits
    action_kw = max(-7.5, min(8.5, action_kw))
    new_soc = current_energy_stored_kwh + action_kw
    if new_soc > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_soc < 0:
      action_kw = -current_energy_stored_kwh

    return action_kw
