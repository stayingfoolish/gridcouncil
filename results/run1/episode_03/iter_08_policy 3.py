class Policy:
  def __init__(self):
    """Predictive cycle optimization with adaptive price modeling"""
    self.price_history = []
    self.pv_history = []
    self.demand_history = []
    self.cycle_profits = []  # Track realized profit per cycle

    self.max_history = 48  # Extend window for better pattern detection
    self.forecast_window = 12  # Look ahead for cycle opportunities

    # Adaptive thresholds
    self.charge_target_ratio = 0.70  # Target SOC for energy accumulation
    self.discharge_target_ratio = 0.25  # Minimum SOC before stopping discharge
    self.pv_priority_threshold = 2.0  # kW excess before prioritizing harvest

    # Price-based decision parameters
    self.profit_margin_required = 0.05  # 5% minimum profit spread
    self.volatility_multiplier = 1.0  # Adapt aggressiveness based on volatility

  def _estimate_price_cycle(self):
    """Estimate next 12-hour price pattern"""
    if len(self.price_history) < 24:
      return {'low': 50, 'high': 50, 'avg': 50, 'volatility': 0}

    # Use rolling statistics with recency weighting
    recent_prices = self.price_history[-12:]
    older_prices = self.price_history[-24:-12]

    recent_avg = sum(recent_prices) / len(recent_prices)
    older_avg = sum(older_prices) / len(older_prices)

    # Momentum-adjusted forecast
    momentum = (recent_avg - older_avg) / older_avg if older_avg > 0 else 0
    forecast_avg = recent_avg * (1 + momentum * 0.3)  # Damped momentum

    recent_vol = max(recent_prices) - min(recent_prices)

    return {
      'avg': forecast_avg,
      'low': forecast_avg - recent_vol * 0.4,
      'high': forecast_avg + recent_vol * 0.6,
      'volatility': recent_vol
    }

  def _calculate_charge_value(self, current_price, cycle_forecast):
    """Estimate profit potential of charging now vs. buying later"""
    future_sell_price = cycle_forecast['high']
    spread = (future_sell_price - current_price) / current_price if current_price > 0 else 0

    return spread

  def _calculate_discharge_value(self, current_price, cycle_forecast):
    """Estimate profit potential of discharging now vs. later"""
    future_buy_price = cycle_forecast['low']
    spread = (current_price - future_buy_price) / current_price if current_price > 0 else 0

    return spread

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Predictive cycle optimization with dynamic thresholds"""

    max_charge_rate = 10.0
    max_discharge_rate = 5.0

    self.price_history.append(current_grid_buy_price)
    self.pv_history.append(current_pv_generation_kw)
    self.demand_history.append(current_demand_kw)

    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)
    if len(self.pv_history) > self.max_history:
      self.pv_history.pop(0)
    if len(self.demand_history) > self.max_history:
      self.demand_history.pop(0)

    soc_ratio = current_energy_stored_kwh / battery_capacity_kwh
    pv_excess = current_pv_generation_kw - current_demand_kw
    demand_deficit = current_demand_kw - current_pv_generation_kw

    cycle_forecast = self._estimate_price_cycle()
    charge_profit_spread = self._calculate_charge_value(current_grid_buy_price, cycle_forecast)
    discharge_profit_spread = self._calculate_discharge_value(current_grid_sell_price, cycle_forecast)

    # Volatility-based position sizing
    volatility_factor = min(1.0 + cycle_forecast['volatility'] / 20, 1.3)
    self.volatility_multiplier = volatility_factor

    action_kw = 0.0

    # Priority 1: PV harvesting (renewable opportunity)
    if pv_excess > self.pv_priority_threshold and soc_ratio < 0.95:
      action_kw = min(pv_excess * 0.95, max_charge_rate)

    # Priority 2: Essential demand support
    elif demand_deficit > 2.0 and soc_ratio > 0.20:
      action_kw = -min(demand_deficit, max_discharge_rate)

    # Priority 3: Profitable arbitrage based on cycle forecast
    elif charge_profit_spread > self.profit_margin_required and soc_ratio < self.charge_target_ratio:
      # Charge when we expect to sell at better price
      charge_amount = min(
        max_charge_rate * self.volatility_multiplier,
        (self.charge_target_ratio - soc_ratio) * battery_capacity_kwh / 2
      )
      action_kw = charge_amount

    elif discharge_profit_spread > self.profit_margin_required and soc_ratio > self.discharge_target_ratio:
      # Discharge when we expect to rebuy at better price
      discharge_amount = min(
        max_discharge_rate * self.volatility_multiplier,
        (soc_ratio - self.discharge_target_ratio) * battery_capacity_kwh / 2
      )
      action_kw = -discharge_amount

    # Priority 4: Conservative positioning near target SOC
    elif soc_ratio < self.discharge_target_ratio:
      # Below minimum: gentle charging to safety buffer
      action_kw = max_charge_rate * 0.3

    elif soc_ratio > self.charge_target_ratio + 0.15:
      # Above target: gentle discharging to reduce exposure
      action_kw = -max_discharge_rate * 0.2

    return action_kw
