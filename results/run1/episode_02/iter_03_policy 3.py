class Policy:
  def __init__(self):
    """Initializes policy with price tracking for momentum detection."""
    self.price_history = []
    self.max_history = 6  # Track last 6 periods for trend detection
    self.soc_target_min = 0.15  # Allow discharge down to 15% SOC
    self.soc_target_max = 0.85  # Don't charge beyond 85% to preserve space

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Dynamic SOC targeting with price momentum awareness."""

    # Use full rates for opportunity exploitation
    charge_rate_kw = battery_capacity_kwh * 0.5
    discharge_rate_kw = battery_capacity_kwh * 0.5

    current_soc = current_energy_stored_kwh / battery_capacity_kwh

    # Track price momentum
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Calculate price trend (recent vs historical average)
    if len(self.price_history) >= 3:
      recent_price = self.price_history[-1]
      historical_avg = sum(self.price_history[:-1]) / len(self.price_history[:-1])
      price_momentum = (recent_price - historical_avg) / (historical_avg + 0.001)
    else:
      price_momentum = 0.0

    action_kw = 0.0

    # Strategy 1: Strong downtrend in prices - CHARGE AGGRESSIVELY
    if price_momentum < -0.15 and current_soc < self.soc_target_max:
      # Prices falling - buy now before they rebound
      charge_available = min(
        charge_rate_kw,
        battery_capacity_kwh * self.soc_target_max - current_energy_stored_kwh
      )
      action_kw = charge_available

    # Strategy 2: Strong uptrend in prices - DISCHARGE AGGRESSIVELY
    elif price_momentum > 0.15 and current_soc > self.soc_target_min:
      # Prices rising - sell now from storage
      discharge_available = min(
        discharge_rate_kw,
        current_energy_stored_kwh - battery_capacity_kwh * self.soc_target_min
      )
      action_kw = -discharge_available

    # Strategy 3: Price arbitrage window - Sell high
    elif current_grid_sell_price > current_grid_buy_price * 1.15:
      if current_soc > self.soc_target_min:
        discharge_available = min(
          discharge_rate_kw * 0.6,
          current_energy_stored_kwh - battery_capacity_kwh * self.soc_target_min
        )
        action_kw = -discharge_available

    # Strategy 4: Cheap pricing window - Buy low
    elif current_grid_buy_price < 0.7 * (self.price_history[-2] if len(self.price_history) > 1 else current_grid_buy_price + 1):
      if current_soc < self.soc_target_max:
        charge_available = min(
          charge_rate_kw * 0.7,
          battery_capacity_kwh * self.soc_target_max - current_energy_stored_kwh
        )
        action_kw = charge_available

    # Strategy 5: Meet local demand if battery available
    elif current_demand_kw > current_pv_generation_kw and current_soc > self.soc_target_min:
      energy_deficit = current_demand_kw - current_pv_generation_kw
      discharge_for_demand = min(
        discharge_rate_kw * 0.4,
        energy_deficit,
        current_energy_stored_kwh - battery_capacity_kwh * self.soc_target_min
      )
      action_kw = -discharge_for_demand

    # Strategy 6: Harvest excess PV if available
    elif current_pv_generation_kw > current_demand_kw and current_soc < self.soc_target_max:
      pv_excess = current_pv_generation_kw - current_demand_kw
      charge_from_pv = min(
        charge_rate_kw * 0.5,
        pv_excess,
        battery_capacity_kwh * self.soc_target_max - current_energy_stored_kwh
      )
      action_kw = charge_from_pv

    return action_kw
