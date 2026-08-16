class Policy:
  def __init__(self):
    """Initialize the policy with state tracking."""
    self.price_history = []
    self.max_history_len = 5
    self.state = "neutral"

  def calculate_price_momentum(self, current_price, history_len=3):
    """Detect if price is trending up or down."""
    if len(self.price_history) < history_len:
      return 0.0
    recent = self.price_history[-history_len:]
    momentum = (recent[-1] - recent[0]) / (recent[0] + 0.001)
    return momentum

  def calculate_target_soc(self,
                          current_buy_price,
                          current_sell_price,
                          price_momentum,
                          battery_capacity_kwh):
    """Calculate target SoC based on price position and momentum."""
    price_spread = current_sell_price - current_buy_price

    if price_momentum < -0.02:
      target_soc = 0.90 * battery_capacity_kwh
    elif price_momentum > 0.02:
      target_soc = 0.20 * battery_capacity_kwh
    elif price_spread > 0.15:
      target_soc = 0.75 * battery_capacity_kwh
    else:
      target_soc = 0.50 * battery_capacity_kwh

    return target_soc

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Determines target action using target SoC and momentum-driven arbitrage."""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_len:
      self.price_history.pop(0)

    action_kw = 0.0
    net_energy = current_pv_generation_kw - current_demand_kw
    price_momentum = self.calculate_price_momentum(current_grid_buy_price)

    target_soc = self.calculate_target_soc(
      current_grid_buy_price,
      current_grid_sell_price,
      price_momentum,
      battery_capacity_kwh
    )

    soc_deficit = target_soc - current_energy_stored_kwh

    charge_threshold = 0.08
    is_low_price = current_grid_buy_price < charge_threshold
    is_downtrend = price_momentum < -0.01
    soc_below_target = current_energy_stored_kwh < target_soc * 0.85

    sell_threshold = 0.18
    is_high_price = current_grid_sell_price > sell_threshold
    is_uptrend = price_momentum > 0.01
    soc_above_target = current_energy_stored_kwh > target_soc * 1.15

    if (is_low_price or is_downtrend) and soc_below_target:
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(8.0, available_capacity)
      self.state = "charging_mode"

    elif (is_high_price or is_uptrend) and soc_above_target and net_energy >= 0:
      available_discharge = current_energy_stored_kwh
      action_kw = -min(7.0, available_discharge)
      self.state = "discharging_mode"

    elif net_energy < 0 and current_energy_stored_kwh > battery_capacity_kwh * 0.15:
      demand_shortfall = abs(net_energy)
      action_kw = -min(5.0, demand_shortfall, current_energy_stored_kwh)

    elif net_energy > 2.0 and current_energy_stored_kwh < battery_capacity_kwh * 0.90:
      charge_from_pv = min(6.0, net_energy, battery_capacity_kwh - current_energy_stored_kwh)
      action_kw = charge_from_pv

    elif abs(soc_deficit) > battery_capacity_kwh * 0.10:
      if soc_deficit > 0:
        action_kw = min(4.0, soc_deficit)
      else:
        action_kw = max(-4.0, soc_deficit)

    new_energy = current_energy_stored_kwh + action_kw
    if new_energy > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_energy < 0:
      action_kw = -current_energy_stored_kwh

    action_kw = max(-7.0, min(8.0, action_kw))

    return action_kw
