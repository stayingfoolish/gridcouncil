class Policy:
  def __init__(self):
    """Momentum-aware battery policy with dynamic reserve management."""
    self.price_history = []
    self.max_history_length = 24
    self.charge_target_soc = 0.85
    self.discharge_target_soc = 0.25

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Momentum-aware arbitrage: exploit predicted price swings."""

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_length:
      self.price_history.pop(0)

    battery_level_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    net_supply = current_pv_generation_kw - current_demand_kw
    price_spread = current_grid_sell_price - current_grid_buy_price

    if len(self.price_history) >= 4:
      recent_avg = sum(self.price_history[-4:]) / 4
      older_avg = sum(self.price_history[-8:-4]) / 4 if len(self.price_history) >= 8 else recent_avg
      momentum = recent_avg - older_avg
    else:
      momentum = 0

    action_kw = 0.0

    if momentum < -0.5 and battery_level_ratio < self.charge_target_soc:
      charge_amount = min(12, 10 + net_supply if net_supply > 0 else 10,
                         battery_capacity_kwh - current_energy_stored_kwh)
      action_kw = charge_amount

    elif momentum > 0.5 and battery_level_ratio > self.discharge_target_soc:
      discharge_amount = min(8, current_energy_stored_kwh)
      action_kw = -discharge_amount

    elif len(self.price_history) >= 2:
      spread_percentage = (price_spread / current_grid_buy_price * 100) if current_grid_buy_price > 0 else 0

      if spread_percentage < -4.5 and battery_level_ratio < 0.8:
        charge_amount = min(10, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_amount

      elif spread_percentage > 4.0 and battery_level_ratio > 0.3:
        discharge_amount = min(8, current_energy_stored_kwh)
        action_kw = -discharge_amount

    else:
      if net_supply > 3.0 and battery_level_ratio < 0.95:
        charge_amount = min(10, net_supply, battery_capacity_kwh - current_energy_stored_kwh)
        action_kw = charge_amount
      elif net_supply < -3.0 and battery_level_ratio > 0.15:
        discharge_amount = min(8, current_energy_stored_kwh, -net_supply)
        action_kw = -discharge_amount

    return action_kw
