class Policy:
  def __init__(self):
    self.price_history = []
    self.extended_history_len = 24
    self.min_arbitrage_margin = 0.02

    self.min_soc_target = 0.05
    self.max_soc_target = 0.95

    self.max_charge_power = 15.0
    self.max_discharge_power = 12.0

    self.momentum_alpha = 0.3
    self.trend_price = None
    self.price_momentum = 0.0

  def estimate_future_price(self, price_history, current_price):
    if len(price_history) < 4:
      return current_price

    recent_prices = price_history[-4:]

    if len(price_history) >= 12:
      older_avg = sum(price_history[-12:-4]) / len(price_history[-12:-4])
    else:
      older_part = price_history[:-4]
      if len(older_part) > 0:
        older_avg = sum(older_part) / len(older_part)
      else:
        older_avg = current_price

    recent_avg = sum(recent_prices) / len(recent_prices)

    momentum = recent_avg - older_avg
    self.price_momentum = momentum

    predicted_price = current_price + momentum * 0.6
    predicted_price = 0.7 * predicted_price + 0.3 * older_avg

    return predicted_price

  def calculate_arbitrage_value(self, current_buy_price, predicted_future_sell_price):
    round_trip_loss = current_buy_price * 0.02
    arbitrage = (predicted_future_sell_price - current_buy_price) - round_trip_loss
    return arbitrage

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.extended_history_len:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    predicted_price = self.estimate_future_price(self.price_history, current_grid_buy_price)
    arbitrage_value = self.calculate_arbitrage_value(current_grid_buy_price, predicted_price)

    action_kw = 0.0

    if predicted_price > current_grid_buy_price and arbitrage_value > self.min_arbitrage_margin:
      available = battery_capacity_kwh - current_energy_stored_kwh
      if available > 0.2:
        confidence = min(abs(self.price_momentum) / 0.15, 1.0)
        soc_urgency = (self.max_soc_target - current_soc) / (self.max_soc_target - self.min_soc_target)
        power_scaling = 0.8 + confidence * 0.4 + soc_urgency * 0.3

        action_kw = min(self.max_charge_power * power_scaling, available)

    elif current_grid_sell_price > predicted_price and (current_grid_sell_price - predicted_price) > self.min_arbitrage_margin:
      available = current_energy_stored_kwh
      if available > 0.2:
        confidence = min(abs(self.price_momentum) / 0.15, 1.0)
        soc_urgency = (current_soc - self.min_soc_target) / (self.max_soc_target - self.min_soc_target)
        power_scaling = 0.8 + confidence * 0.4 + soc_urgency * 0.3

        action_kw = -min(self.max_discharge_power * power_scaling, available)

    else:
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 0.5 and current_soc < self.max_soc_target - 0.1:
        action_kw = min(pv_excess * 0.9, self.max_charge_power * 0.25,
                       battery_capacity_kwh - current_energy_stored_kwh)
      elif pv_excess < -0.5 and current_soc > self.min_soc_target + 0.1:
        action_kw = -min(abs(pv_excess) * 0.7, self.max_discharge_power * 0.25,
                        current_energy_stored_kwh)

    new_soc = current_energy_stored_kwh + action_kw
    if new_soc > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_soc < 0:
      action_kw = -current_energy_stored_kwh

    return action_kw
