class Policy:
  def __init__(self):
    self.max_discharge_rate = 5
    self.max_charge_rate = 10

    self.price_history = []
    self.soc_history = []
    self.cost_history = []

    # Window-based thresholds
    self.forecast_window = 6
    self.charge_percentile = 0.30  # Buy in bottom 30% of forecast
    self.discharge_percentile = 0.75  # Sell in top 25% of forecast

    # Utilization enforcement
    self.cycles_since_discharge = 0
    self.max_hold_cycles = 8
    self.min_discharge_soc = 0.35
    self.critical_charge_soc = 0.25

    # Price tracking
    self.price_volatility = 0.0
    self.lookback_window = 12

    # Exponential smoothing for trend
    self.price_trend = 0.0
    self.trend_alpha = 0.3

  def forecast_price_window(self):
    """Create 6-step price forecast using exponential decay and trend"""
    if len(self.price_history) < 3:
      current = self.price_history[-1] if self.price_history else 0
      return [current] * self.forecast_window

    current_price = self.price_history[-1]
    price_change = self.price_history[-1] - self.price_history[-2]

    # Update trend with exponential smoothing
    self.price_trend = self.trend_alpha * price_change + (1 - self.trend_alpha) * self.price_trend

    # Decay trend influence over forecast horizon
    forecast = []
    for step in range(self.forecast_window):
      decay_factor = 0.85 ** step  # Exponential decay
      predicted = current_price + self.price_trend * decay_factor
      forecast.append(max(0.01, predicted))

    return forecast

  def get_price_quantiles(self, forecast):
    """Get charge and discharge threshold from forecast"""
    sorted_forecast = sorted(forecast)
    charge_idx = max(0, int(len(sorted_forecast) * self.charge_percentile))
    discharge_idx = min(len(sorted_forecast) - 1, int(len(sorted_forecast) * self.discharge_percentile))

    charge_threshold = sorted_forecast[charge_idx]
    discharge_threshold = sorted_forecast[discharge_idx]

    return charge_threshold, discharge_threshold

  def calculate_volatility(self):
    """Calculate recent price volatility"""
    if len(self.price_history) < self.lookback_window:
      return 0.0

    recent_prices = self.price_history[-self.lookback_window:]
    mean_price = sum(recent_prices) / len(recent_prices)
    variance = sum((p - mean_price) ** 2 for p in recent_prices) / len(recent_prices)
    self.price_volatility = variance ** 0.5
    return self.price_volatility

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:

    battery_fill_ratio = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
    available_capacity = battery_capacity_kwh - current_energy_stored_kwh

    # Update history
    self.price_history.append(current_grid_buy_price)
    self.soc_history.append(battery_fill_ratio)
    if len(self.price_history) > 20:
      self.price_history.pop(0)
      self.soc_history.pop(0)

    # Get volatility and forecast
    volatility = self.calculate_volatility()
    forecast = self.forecast_price_window()
    charge_threshold, discharge_threshold = self.get_price_quantiles(forecast)

    # Increment hold counter
    self.cycles_since_discharge += 1

    # **DISCHARGE LOGIC** - Prioritize discharge to improve utilization
    if battery_fill_ratio > self.min_discharge_soc:
      # Sell if price is favorable OR forced discharge cycle
      forced_discharge = self.cycles_since_discharge >= self.max_hold_cycles
      price_favorable = current_grid_sell_price > charge_threshold * 1.05  # 5% margin

      if forced_discharge or (price_favorable and current_grid_sell_price > current_grid_buy_price * 0.95):
        # Aggressive discharge when favorable
        action = -min(self.max_discharge_rate, current_energy_stored_kwh * 0.6)
        self.cycles_since_discharge = 0
        return action

    # **CHARGE LOGIC** - Buy when price is low
    if battery_fill_ratio < 0.90:
      # Charge if price is in buy window or battery critically low
      price_attractive = current_grid_buy_price < charge_threshold * 0.98
      critical_low = battery_fill_ratio < self.critical_charge_soc

      if price_attractive or critical_low:
        action = min(self.max_charge_rate, available_capacity * 0.8)
        return action

    # **HOLD** - No action needed
    return 0.0
