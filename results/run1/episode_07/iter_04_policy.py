class Policy:
  def __init__(self):
    """Initializes volatility-adaptive trend-following policy."""
    self.max_charge_rate = 12.0
    self.max_discharge_rate = 10.0

    # Dynamic threshold parameters (no longer fixed)
    self.volatility_lookback = 5  # Use last 5 price points to calculate volatility
    self.base_sell_threshold = 0.12  # Base euros/kWh - adjusted by volatility
    self.base_buy_threshold = 0.11   # Base euros/kWh - adjusted by volatility

    # Volatility-responsive SOC targets
    self.min_soc_base = 0.15  # Lower reserve margin (aggressive)
    self.max_soc_base = 0.85  # Utilize up to 85% capacity

    # Price trend parameters
    self.trend_window = 3  # Look at last 3 prices for trend
    self.strong_uptrend_threshold = 0.02  # Price increasing strongly
    self.strong_downtrend_threshold = -0.02  # Price decreasing strongly

    # State tracking
    self.price_history = []
    self.max_history_len = 10

  def calculate_dynamic_thresholds(self, volatility):
    """Thresholds expand with volatility to stay relevant."""
    # Higher volatility = wider bands for arbitrage opportunities
    volatility_factor = max(0.5, volatility / 15.0)  # Normalize to market conditions

    sell_threshold = self.base_sell_threshold + (volatility_factor * 0.03)
    buy_threshold = self.base_buy_threshold - (volatility_factor * 0.03)

    return sell_threshold, buy_threshold

  def detect_price_trend(self, current_price):
    """Detect if prices are trending up or down (bullish/bearish)."""
    self.price_history.append(current_price)
    if len(self.price_history) > self.max_history_len:
      self.price_history.pop(0)

    if len(self.price_history) < self.trend_window + 1:
      return 0  # Neutral - not enough data

    recent = self.price_history[-self.trend_window:]
    trend = sum([recent[i+1] - recent[i] for i in range(len(recent)-1)]) / len(recent)
    return trend

  def calculate_dynamic_soc_target(self, volatility, price_trend, current_soc):
    """SOC targets respond to market conditions."""
    # High volatility = want more flexibility (higher SOC)
    volatility_factor = min(0.3, volatility / 20.0)

    # Downtrend = prepare to buy by lowering SOC (create capacity)
    # Uptrend = prepare to sell by raising SOC (build reserves)
    trend_factor = 0.15 if price_trend > self.strong_uptrend_threshold else -0.1 if price_trend < self.strong_downtrend_threshold else 0

    target_soc = 0.50 + volatility_factor + trend_factor
    target_soc = max(self.min_soc_base, min(self.max_soc_base, target_soc))

    return target_soc

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
    price_volatility: float = 10.0,
  ) -> float:
    """Volatility-adaptive decision making with trend awareness."""

    current_soc = current_energy_stored_kwh / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    # Calculate dynamic thresholds based on volatility
    sell_threshold, buy_threshold = self.calculate_dynamic_thresholds(price_volatility)

    # Detect price trend
    price_trend = self.detect_price_trend(current_grid_buy_price)

    # Dynamic SOC targets
    target_soc = self.calculate_dynamic_soc_target(price_volatility, price_trend, current_soc)

    # Calculate reserve and capacity
    min_energy = battery_capacity_kwh * self.min_soc_base
    available_to_discharge = max(0, current_energy_stored_kwh - min_energy)
    available_to_charge = max(0, battery_capacity_kwh - current_energy_stored_kwh)

    # Strategy: Aggressive arbitrage on price extremes
    if current_grid_sell_price > sell_threshold and available_to_discharge > 0:
      max_discharge = min(self.max_discharge_rate, available_to_discharge)
      return -max_discharge

    # Strategy: Strong buying opportunity when prices falling
    if current_grid_buy_price < buy_threshold and available_to_charge > 0 and current_soc < target_soc:
      return min(self.max_charge_rate, available_to_charge)

    # Strategy: Opportunistic charging during strong downtrend (prepare for reversal)
    if price_trend < self.strong_downtrend_threshold and available_to_charge > 0 and current_soc < target_soc + 0.1:
      return min(self.max_charge_rate * 0.8, available_to_charge)

    # Strategy: PV utilization (secondary priority after arbitrage)
    surplus_pv = max(0, current_pv_generation_kw - current_demand_kw)
    if surplus_pv > 0.1 and available_to_charge > 0 and current_soc < target_soc:
      return min(self.max_charge_rate, surplus_pv, available_to_charge)

    # Strategy: Tactical discharge to maintain target SOC when above it
    if current_soc > target_soc + 0.05 and current_grid_sell_price > (self.base_buy_threshold + 0.01):
      # Prevent over-charging by selling excess above target
      max_discharge = min(self.max_discharge_rate, available_to_discharge)
      return -max_discharge * 0.6

    # Strategy: Use battery for demand when reserves healthy AND no better arbitrage
    deficit = max(0, current_demand_kw - current_pv_generation_kw)
    if deficit > 0.1 and available_to_discharge > 0 and current_soc > target_soc + 0.1:
      max_discharge = min(self.max_discharge_rate, available_to_discharge, deficit)
      return -max_discharge

    return 0.0
