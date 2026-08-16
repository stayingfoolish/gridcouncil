class Policy:
  def __init__(self):
    """Momentum-based market-making policy with volatility scaling"""
    self.price_history = []
    self.max_history = 30
    self.momentum_window = 5  # Look at recent 5 prices for trend

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Momentum-driven arbitrage with volatility-adaptive thresholds"""
    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    # Track price history
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Calculate volatility-scaled metrics
    avg_price = sum(self.price_history) / len(self.price_history) if self.price_history else current_grid_buy_price
    price_std = (sum((p - avg_price)**2 for p in self.price_history) / len(self.price_history)) ** 0.5 if len(self.price_history) > 1 else 0
    volatility_factor = max(1.0, price_std / avg_price if avg_price > 0 else 1.0)  # Normalized volatility

    # Calculate price momentum (recent trend)
    if len(self.price_history) >= self.momentum_window:
      recent_prices = self.price_history[-self.momentum_window:]
      price_momentum = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] if recent_prices[0] > 0 else 0
    else:
      price_momentum = 0

    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh

    # Volatility-scaled thresholds: higher volatility = more aggressive arbitrage
    charge_threshold = 1.0 - (0.15 * volatility_factor)  # Buy when price below this multiple of avg
    discharge_threshold = 1.0 + (0.15 * volatility_factor)  # Sell when price above this multiple of avg

    action_kw = 0.0

    # STRATEGY 1: Exploit Price Momentum Reversions (High Priority)
    # If price just dropped significantly, buy to catch rebound
    if price_momentum < -0.05 and soc_ratio < 0.80:
      if current_grid_buy_price < avg_price * charge_threshold:
        charge_amount = max_charge_rate * (1 + volatility_factor * 0.5)
        action_kw = min(charge_amount, max_charge_rate)
        return action_kw

    # If price just spiked, discharge to capture gains
    if price_momentum > 0.05 and soc_ratio > 0.25:
      if current_grid_sell_price > avg_price * discharge_threshold:
        discharge_amount = max_discharge_rate * (1 + volatility_factor * 0.5)
        action_kw = -min(discharge_amount, max_discharge_rate, current_energy_stored_kwh)
        return action_kw

    # STRATEGY 2: SOC-Based Reserve Management (Secondary)
    # Low SOC: aggressive charging regardless of price (safety reserve)
    if soc_ratio < 0.25:
      if current_pv_generation_kw > current_demand_kw:
        charge_amount = min(current_pv_generation_kw - current_demand_kw, max_charge_rate)
        action_kw = charge_amount
      else:
        action_kw = max_charge_rate * 0.8

    # Medium-low SOC: charge on favorable prices with volatility scaling
    elif soc_ratio < 0.50:
      if current_grid_buy_price < avg_price * charge_threshold:
        action_kw = max_charge_rate * (0.6 + volatility_factor * 0.3)
      elif current_pv_generation_kw > current_demand_kw * 1.2:
        action_kw = min(current_pv_generation_kw - current_demand_kw, max_charge_rate * 0.4)

    # Medium-high SOC: focus on discharge opportunities
    elif soc_ratio < 0.75:
      if current_grid_sell_price > avg_price * discharge_threshold:
        action_kw = -max_discharge_rate * 0.8

    # High SOC: aggressive discharge to create arbitrage opportunity
    else:
      if current_grid_sell_price > avg_price:
        action_kw = -max_discharge_rate
      elif current_demand_kw > 0:
        action_kw = -min(current_demand_kw * 0.5, max_discharge_rate)

    return action_kw
