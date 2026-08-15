class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    pass

  def take_action(self,
    # energy stored in the battery [kWh]
    current_energy_stored_kwh: float,
    # PV power generation [kW]
    current_pv_generation_kw: float,
    # household power demand [kW]
    current_demand_kw: float,
    # grid purchase price [euro/kWh]
    current_grid_buy_price: float,
    # grid feed-in tariff (sell price) [euro/kWh]
    current_grid_sell_price: float,
    # Maximum battery capacity [kWh]
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state.

    Returns:
      float: The target power for the battery [kW]
        positive: charging; negative: discharging;
        zero: no action
    """

    max_charge_power = 10.0
    max_discharge_power = 5.0

    available_charge_capacity = battery_capacity_kwh - current_energy_stored_kwh
    available_discharge_capacity = current_energy_stored_kwh

    can_charge = min(max_charge_power, available_charge_capacity)
    can_discharge = min(max_discharge_power, available_discharge_capacity)

    generation_demand_diff = current_pv_generation_kw - current_demand_kw

    action_kw = 0.0

    if generation_demand_diff > 0:
        excess_generation = generation_demand_diff

        if current_energy_stored_kwh < battery_capacity_kwh * 0.85:
            action_kw = min(excess_generation, can_charge)

    else:
        demand_deficit = -generation_demand_diff

        if can_discharge > 0:
            battery_soc_ratio = current_energy_stored_kwh / max(battery_capacity_kwh, 0.1)

            if battery_soc_ratio > 0.35 or current_grid_buy_price > current_grid_sell_price * 1.4:
                action_kw = -min(demand_deficit, can_discharge)

    return action_kw
