class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    self.price_volatility = 10.1

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
    volatility_factor = self.price_volatility / 100.0
    base_low_buy_price = 0.1
    base_high_buy_price = 0.2
    base_sell_price = 0.35

    adaptive_low_buy = base_low_buy_price - (volatility_factor * 0.03)
    adaptive_high_buy = base_high_buy_price - (volatility_factor * 0.02)
    adaptive_sell = base_sell_price + (volatility_factor * 0.05)

    dynamic_reserve = min(0.2, 0.1 + volatility_factor * 0.2)

    min_reserve_energy = battery_capacity_kwh * dynamic_reserve
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    action_kw = 0.0

    demand_shortfall = max(0.0, current_demand_kw - current_pv_generation_kw)

    if current_energy_stored_kwh > min_reserve_energy and demand_shortfall > 0:
        max_discharge = min(5.0, current_energy_stored_kwh - min_reserve_energy)
        action_kw = -min(max_discharge, demand_shortfall)
    elif demand_shortfall > 0 and current_grid_buy_price > adaptive_sell and current_energy_stored_kwh > min_reserve_energy:
        max_discharge = min(5.0, current_energy_stored_kwh - min_reserve_energy)
        action_kw = -min(max_discharge, demand_shortfall)

    if action_kw == 0.0 and available_capacity > 0.0:
        if current_grid_buy_price < adaptive_low_buy:
            max_charge = min(10.0, available_capacity)
            action_kw = max_charge
        elif current_grid_buy_price < adaptive_high_buy:
            max_charge = min(7.0, available_capacity)
            action_kw = max_charge * 0.6
        else:
            excess_pv = max(0.0, current_pv_generation_kw - current_demand_kw)
            if excess_pv > 0.0:
                charge_from_pv = min(excess_pv, 10.0, available_capacity)
                action_kw = charge_from_pv

    if action_kw == 0.0 and current_energy_stored_kwh > min_reserve_energy:
        if current_grid_sell_price > adaptive_sell:
            max_discharge = min(5.0, current_energy_stored_kwh - min_reserve_energy)
            action_kw = -max_discharge

    action_kw = max(-5.0, min(10.0, action_kw))

    return action_kw
