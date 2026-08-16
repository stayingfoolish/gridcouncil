class Policy:
  def __init__(self):
    self.price_history = []
    self.spread_history = []
    self.action_history = []

    # Simplified, aggressive parameters
    self.max_history_len = 20  # Shorter window for faster adaptation
    self.buy_trigger_threshold = -0.15  # Price 15% below moving average = BUY
    self.sell_trigger_threshold = 0.18  # Price 18% above moving average = SELL
    self.min_spread_threshold = 0.5  # Only trade spreads > 0.5

    # SOC targets for arbitrage positions
    self.soc_buy_ready = 0.25  # Target SOC before buying (room for charge)
    self.soc_sell_ready = 0.75  # Target SOC before selling (room to discharge)

    # Power settings - much more aggressive
    self.max_charge_power = 9.5  # Up from 8.0 * 1.3
    self.max_discharge_power = 8.5  # Up from 7.0 * 1.3
    self.volatility_boost_factor = 0.5  # Extra power when market is volatile

  def calculate_price_trend(self, prices):
    """Calculate whether prices are trending up or down"""
    if len(prices) < 3:
      return 0.0

    recent = prices[-3:]
    trend = (recent[-1] - recent[0]) / (abs(recent[0]) + 0.01)
    return max(-1.0, min(1.0, trend))

  def identify_arbitrage_opportunity(self, buy_price, sell_price, current_soc, battery_capacity):
    """Return (opportunity_type, intensity) where type is 'buy', 'sell', or 'none'"""
    spread = sell_price - buy_price

    # Strong arbitrage signals based on spread alone
    if spread > self.min_spread_threshold:
      # Good spread for arbitrage - prefer selling
      if current_soc > 0.4:  # Only sell if we have energy
        return ("sell", min(1.0, spread / 1.5))
      else:
        return ("none", 0.0)

    elif spread < -self.min_spread_threshold:
      # Good spread for arbitrage - prefer buying
      available_capacity = 1.0 - current_soc
      if available_capacity > 0.15:  # Only buy if we have room
        return ("buy", min(1.0, abs(spread) / 1.5))
      else:
        return ("none", 0.0)
    else:
      return ("none", 0.0)

  def calculate_price_signal(self, price_history, current_price):
    """Return -1 (strong sell signal) to +1 (strong buy signal)"""
    if len(price_history) < 5:
      return 0.0

    price_avg = sum(price_history) / len(price_history)
    deviation = (current_price - price_avg) / (price_avg + 0.01)

    # Deviation from moving average is the primary signal
    if deviation < self.buy_trigger_threshold:
      return min(-1.0, deviation * 3.0)  # Strong buy signal
    elif deviation > self.sell_trigger_threshold:
      return max(1.0, deviation * 2.5)  # Strong sell signal
    else:
      return deviation * 1.5  # Neutral zone

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history_len:
      self.price_history.pop(0)

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    spread = current_grid_sell_price - current_grid_buy_price

    # Primary: Check for arbitrage opportunities
    arb_type, arb_intensity = self.identify_arbitrage_opportunity(
      current_grid_buy_price, current_grid_sell_price, current_soc, battery_capacity_kwh
    )

    # Secondary: Check price deviation from moving average
    price_signal = self.calculate_price_signal(self.price_history, current_grid_buy_price)

    # Volatility boost - more aggressive when market is volatile
    recent_prices = self.price_history[-5:] if len(self.price_history) >= 5 else self.price_history
    if len(recent_prices) >= 3:
      volatility = max(abs(p1 - p2) for p1, p2 in zip(recent_prices[:-1], recent_prices[1:])) / (sum(recent_prices)/len(recent_prices) + 0.01)
      volatility = min(1.0, volatility)
    else:
      volatility = 0.0

    power_boost = 1.0 + (volatility * self.volatility_boost_factor)

    # Decision logic: Hierarchical priority
    action_kw = 0.0

    if arb_type == "buy" and arb_intensity > 0:
      # Arbitrage buy opportunity - charge aggressively
      available = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = (self.max_charge_power * power_boost) * arb_intensity
      action_kw = min(charge_power, available)

    elif arb_type == "sell" and arb_intensity > 0:
      # Arbitrage sell opportunity - discharge aggressively
      available = current_energy_stored_kwh
      discharge_power = (self.max_discharge_power * power_boost) * arb_intensity
      action_kw = -min(discharge_power, available)

    elif price_signal < -0.5 and current_soc < 0.6:
      # Very low price window detected - opportunistic buying
      available = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = (self.max_charge_power * 0.8) * abs(price_signal)
      action_kw = min(charge_power, available)

    elif price_signal > 0.5 and current_soc > 0.4:
      # Very high price window detected - opportunistic selling
      available = current_energy_stored_kwh
      discharge_power = (self.max_discharge_power * 0.8) * price_signal
      action_kw = -min(discharge_power, available)

    else:
      # Fallback: Light PV balancing when no strong signals
      pv_excess = current_pv_generation_kw - current_demand_kw
      if pv_excess > 0.5 and current_soc < 0.9:
        action_kw = min(pv_excess * 0.6, self.max_charge_power * 0.3, battery_capacity_kwh - current_energy_stored_kwh)
      elif pv_excess < -0.5 and current_soc > 0.1:
        action_kw = -min(abs(pv_excess) * 0.4, self.max_discharge_power * 0.3, current_energy_stored_kwh)

    # Hard boundaries
    action_kw = max(-self.max_discharge_power * power_boost,
                   min(self.max_charge_power * power_boost, action_kw))

    # Clamp to battery limits
    new_soc = current_energy_stored_kwh + action_kw
    if new_soc > battery_capacity_kwh:
      action_kw = battery_capacity_kwh - current_energy_stored_kwh
    elif new_soc < 0:
      action_kw = -current_energy_stored_kwh

    self.action_history.append(action_kw)
    return action_kw
