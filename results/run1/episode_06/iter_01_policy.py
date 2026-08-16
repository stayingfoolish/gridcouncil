class Policy:
  def __init__(self):
    self.buy_price_baseline = 100.0  # Fallback reference
    self.sell_price_baseline = 100.0

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    pv_surplus = current_pv_generation_kw - current_demand_kw
    battery_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Update baselines as moving average
    self.buy_price_baseline = self.buy_price_baseline * 0.8 + current_grid_buy_price * 0.2
    self.sell_price_baseline = self.sell_price_baseline * 0.8 + current_grid_sell_price * 0.2

    # Calculate opportunity magnitude
    buy_discount_ratio = self.buy_price_baseline / current_grid_buy_price if current_grid_buy_price > 0 else 1.0
    sell_premium_ratio = current_grid_sell_price / self.sell_price_baseline if self.sell_price_baseline > 0 else 1.0

    # Strategy 1: PV surplus charging (UNCHANGED)
    if pv_surplus > 0.5 and battery_soc < 0.90:
      action_kw = min(pv_surplus, 10.0)
      return action_kw

    # Strategy 2: PV deficit discharge (UNCHANGED)
    if pv_surplus < -0.5 and battery_soc > 0.15:
      action_kw = max(-min(abs(pv_surplus), 5.0), -5.0)
      return action_kw

    # Strategy 3: IMPROVED - Dynamic charging based on buy price opportunity
    if current_grid_buy_price < current_grid_sell_price * 0.5 and battery_soc < 0.75:
      # Scale action from 3 kW (10% discount) to 12 kW (90% discount relative to baseline)
      action_kw = 3.0 + (buy_discount_ratio - 1.0) * 15.0
      action_kw = min(action_kw, 12.0)
      return max(action_kw, 3.0)

    # Strategy 4: IMPROVED - Dynamic discharge based on sell price opportunity
    if current_grid_sell_price > current_grid_buy_price * 1.8 and battery_soc > 0.35:
      # Scale action from 2 kW (20% premium) to 8 kW (80% premium relative to baseline)
      action_kw = -2.0 - (sell_premium_ratio - 1.0) * 12.0
      action_kw = max(action_kw, -8.0)
      return min(action_kw, -2.0)

    # Strategy 5: Critical charging (UNCHANGED)
    if battery_soc < 0.20 and current_grid_buy_price < current_grid_sell_price * 0.7:
      action_kw = 10.0
      return action_kw

    return 0.0
