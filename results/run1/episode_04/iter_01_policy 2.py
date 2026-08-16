class Policy:
  def __init__(self):
    """Initializes the policy with tracking for volatility calculation."""
    self.price_history = []
    self.max_history_length = 24

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state.

    Returns:
      float: The target power for the battery [kW]
        positive: charging; negative: discharging;
        zero: no action
    """
    # Track price history for volatility calculation
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)

    # Calculate price volatility
    if len(self.price_history) > 1:
      avg_price = sum(self.price_history) / len(self.price_history)
      variance = sum((p - avg_price) ** 2 for p in self.price_history) / len(self.price_history)
      price_volatility = variance ** 0.5 if variance > 0 else 0
    else:
      price_volatility = 0

    # Calculate battery level ratio
    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Determine action based on supply/demand situation
    net_supply = current_pv_generation_kw - current_demand_kw

    action_kw = 0.0

    # Case 1: High PV generation (excess supply)
    if net_supply > 2.0:
      if battery_level_ratio < 0.9:
        charge_amount = min(10, net_supply, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_amount
      else:
        action_kw = 0.0

    # Case 2: High demand (deficit)
    elif net_supply < -2.0:
      if battery_level_ratio > 0.2:
        discharge_amount = min(5, current_energy_stored_kwh, -net_supply)
        action_kw = -discharge_amount
      else:
        action_kw = 0.0

    # Case 3: Balanced supply and demand - Dynamic Price Spread Arbitrage
    else:
      price_spread = current_grid_sell_price - current_grid_buy_price
      spread_percentage = (price_spread / current_grid_buy_price * 100) if current_grid_buy_price > 0 else 0

      # Volatility-adjusted thresholds: reduce risk during high volatility
      volatility_factor = min(20.1 / (price_volatility + 0.1), 2.0)
      charge_threshold = max(0.5, 0.8 * volatility_factor)
      discharge_threshold = min(0.8, 0.6 / volatility_factor)

      # Charge when spread is negative (buying cheap, will sell expensive later)
      if spread_percentage < -3.0 and battery_level_ratio < charge_threshold:
        charge_amount = min(8, 10, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_amount
      # Discharge when spread is positive and large enough to justify action
      elif spread_percentage > 5.0 and battery_level_ratio > discharge_threshold:
        discharge_amount = min(5, current_energy_stored_kwh)
        action_kw = -discharge_amount
      else:
        action_kw = 0.0

    return action_kw
