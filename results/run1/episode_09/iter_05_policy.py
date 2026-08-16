class Policy:
  def __init__(self):
    self.margin_history = []
    self.soc_history = []
    self.demand_history = []
    self.cycle_count = 0
    self.last_action_was_charge = None

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    max_charge_rate = 10.0
    max_discharge_rate = 8.0

    margin = current_grid_sell_price - current_grid_buy_price
    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    net_flow = current_pv_generation_kw - current_demand_kw

    # Extended history for pattern recognition (500 steps instead of 100)
    self.margin_history.append(margin)
    self.soc_history.append(soc_ratio)
    self.demand_history.append(current_demand_kw)

    if len(self.margin_history) > 500:
      self.margin_history.pop(0)
      self.soc_history.pop(0)
      self.demand_history.pop(0)

    # STRATEGY 1: AGGRESSIVE ARBITRAGE IN EXCEPTIONAL WINDOWS
    # When margin is in top 10%, charge aggressively to prepare for selling
    # When margin is in bottom 10%, discharge aggressively to capture profits

    if len(self.margin_history) >= 20:
      sorted_margins = sorted(self.margin_history)
      buy_threshold = sorted_margins[max(0, int(len(sorted_margins) * 0.15))]
      sell_threshold = sorted_margins[min(len(sorted_margins)-1, int(len(sorted_margins) * 0.85))]
      exceptional_buy = sorted_margins[max(0, int(len(sorted_margins) * 0.05))]
      exceptional_sell = sorted_margins[min(len(sorted_margins)-1, int(len(sorted_margins) * 0.95))]
    else:
      buy_threshold = -0.3
      sell_threshold = 0.3
      exceptional_buy = -0.8
      exceptional_sell = 1.0

    # STRATEGY 2: DEMAND ANTICIPATION
    # If demand is historically high and we have SOC, prepare to discharge
    # If demand is historically low and we have PV, prepare to charge

    avg_demand = sum(self.demand_history[-50:]) / min(50, len(self.demand_history)) if self.demand_history else current_demand_kw
    demand_ratio = current_demand_kw / max(avg_demand, 0.1)

    # CHARGING STRATEGY
    if margin < buy_threshold and soc_ratio < 0.90:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(
        max_charge_rate * (0.6 + 0.4 * min(1.0, abs(margin) / max(abs(buy_threshold), 0.1))),
        battery_space,
        current_pv_generation_kw + max_charge_rate
      )
      if charge_power > 0.3:
        self.cycle_count += 0.5
        return charge_power

    # AGGRESSIVE CHARGING IN EXCEPTIONAL BUY CONDITIONS
    if margin < exceptional_buy and soc_ratio < 0.85:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(
        max_charge_rate * 0.95,
        battery_space,
        current_pv_generation_kw + max_charge_rate * 0.8
      )
      if charge_power > 0.3:
        self.cycle_count += 0.5
        return charge_power

    # DISCHARGING STRATEGY
    if margin > sell_threshold and soc_ratio > 0.20:
      discharge_power = min(
        max_discharge_rate * (0.5 + 0.5 * min(1.0, margin / max(abs(sell_threshold), 0.1))),
        current_energy_stored_kwh
      )
      if discharge_power > 0.3:
        self.cycle_count += 0.5
        return -discharge_power

    # AGGRESSIVE DISCHARGING IN EXCEPTIONAL SELL CONDITIONS
    if margin > exceptional_sell and soc_ratio > 0.30:
      discharge_power = min(
        max_discharge_rate * 0.90,
        current_energy_stored_kwh
      )
      if discharge_power > 0.3:
        self.cycle_count += 0.5
        return -discharge_power

    # STRATEGY 3: DEMAND-DRIVEN POSITIONING
    # When demand is unexpectedly high, discharge to help and profit
    if demand_ratio > 1.3 and soc_ratio > 0.25 and margin > -0.2:
      discharge_power = min(max_discharge_rate * 0.7, current_energy_stored_kwh)
      if discharge_power > 0.2:
        self.cycle_count += 0.5
        return -discharge_power

    # STRATEGY 4: FORCED POSITIONING BASED ON EXTREMES
    if soc_ratio < 0.08:
      battery_space = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(max_charge_rate, battery_space)
      return charge_power

    if soc_ratio > 0.97:
      discharge_power = min(max_discharge_rate * 0.6, current_energy_stored_kwh)
      return -discharge_power

    return 0.0
