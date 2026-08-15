class Policy:
  def __init__(self):
    pass

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    # Calculate energy balance and battery state
    pv_surplus = current_pv_generation_kw - current_demand_kw
    battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Strategy 1: Prioritize charging with excess PV
    if pv_surplus > 0.5 and battery_soc < 0.90:
      # We have excess PV and battery not full
      action_kw = min(pv_surplus, 10.0)
      return action_kw

    # Strategy 2: Discharge when demand exceeds PV and battery is charged
    if pv_surplus < -0.5 and battery_soc > 0.15:
      # Demand exceeds PV and we have stored energy
      action_kw = max(-min(abs(pv_surplus), 5.0), -5.0)
      return action_kw

    # Strategy 3: Opportunistic charging when buying price is very low
    if current_grid_buy_price < current_grid_sell_price * 0.5 and battery_soc < 0.75:
      # Buying is much cheaper than selling price, store energy
      action_kw = 8.0
      return action_kw

    # Strategy 4: Discharge when selling price is very high
    if current_grid_sell_price > current_grid_buy_price * 1.8 and battery_soc > 0.35:
      # High selling opportunity, discharge stored energy
      action_kw = -3.5
      return action_kw

    # Strategy 5: Smart charging when battery is low and prices favor it
    if battery_soc < 0.20 and current_grid_buy_price < current_grid_sell_price * 0.7:
      # Battery critically low and buying is favorable
      action_kw = 10.0
      return action_kw

    # Default: no action
    return 0.0
