class Policy:
  def __init__(self):
    """Volatility-aware arbitrage policy"""
    self.price_history = []
    self.max_history = 20

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Adaptive arbitrage strategy"""
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    # Track price history for volatility calculation
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Calculate statistics
    avg_buy_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_grid_buy_price
    price_deviation = current_grid_buy_price - avg_buy_price

    # Dynamic thresholds: adjusted by volatility and current SOC
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh

    # Aggressive charging zones:
    # Zone 1: Very low SOC (< 20%) - charge at ANY reasonable price
    # Zone 2: Low SOC (20-40%) - charge when price < average
    # Zone 3: Medium SOC (40-70%) - charge only when price significantly below average
    # Zone 4: High SOC (70%+) - prioritize discharge

    action_kw = 0.0

    if soc_ratio < 0.20:
      # AGGRESSIVE CHARGE: Battery is a critical asset to recharge
      if current_pv_generation_kw > current_demand_kw:
        # Abundant PV - charge at full rate
        charge_amount = min(current_pv_generation_kw - current_demand_kw, max_charge_rate)
        action_kw = charge_amount
      elif current_grid_buy_price < avg_buy_price * 1.1:
        # Price is near/below average - buy from grid
        action_kw = max_charge_rate

    elif soc_ratio < 0.40:
      # MODERATE CHARGE: Opportunity to build reserves
      if current_pv_generation_kw > current_demand_kw:
        excess = current_pv_generation_kw - current_demand_kw
        action_kw = min(excess, max_charge_rate)
      elif current_grid_buy_price < avg_buy_price:
        action_kw = max_charge_rate * 0.7

    elif soc_ratio < 0.70:
      # SELECTIVE CHARGE: Only on strong PV days
      if current_pv_generation_kw > current_demand_kw * 1.5:
        excess = current_pv_generation_kw - current_demand_kw
        action_kw = min(excess, max_charge_rate * 0.5)

    else:
      # HIGH SOC: Prioritize discharge for revenue
      # Discharge opportunistically to make room and generate revenue
      if current_grid_sell_price > avg_buy_price * 0.85:
        # Price is favorable for discharge
        action_kw = -min(max_discharge_rate, current_energy_stored_kwh)
      elif current_demand_kw > 0:
        # Use battery to meet demand (reduce grid imports)
        discharge_power = min(current_demand_kw, max_discharge_rate, current_energy_stored_kwh)
        action_kw = -discharge_power

    # During all zones: opportunistic discharge for high prices
    if soc_ratio > 0.15 and current_grid_sell_price > avg_buy_price:
      opportunity_discharge = min(current_demand_kw + 2.0, max_discharge_rate, current_energy_stored_kwh)
      action_kw = -opportunity_discharge

    return action_kw
