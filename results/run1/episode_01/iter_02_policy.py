class Policy:
  def __init__(self):
    """Initializes the policy with history tracking"""
    self.price_history = []
    self.max_history = 20

  def take_action(self,
    current_energy_stored_kwh: float,
    current_pv_generation_kw: float,
    current_demand_kw: float,
    current_grid_buy_price: float,
    current_grid_sell_price: float,
    battery_capacity_kwh: float,
  ) -> float:
    """Dynamic battery management focused on price arbitrage and PV firming"""

    # Update price history for volatility calculation
    self.price_history.append(current_grid_buy_price)
    if len(self.price_history) > self.max_history:
      self.price_history.pop(0)

    # Calculate dynamic parameters based on market conditions
    price_spread = current_grid_sell_price - current_grid_buy_price

    # Volatility-aware thresholds (higher volatility = more aggressive)
    if len(self.price_history) >= 3:
      price_volatility = (max(self.price_history) - min(self.price_history)) / (sum(self.price_history) / len(self.price_history) + 0.01)
    else:
      price_volatility = 0

    current_soc = current_energy_stored_kwh / battery_capacity_kwh
    net_power = current_pv_generation_kw - current_demand_kw

    # Dynamic SOC targets based on price signal
    if current_grid_buy_price < 0.5 * current_grid_sell_price:  # Extremely low buy price
      target_soc_min = 0.10  # Aggressively charge
      target_soc_max = 0.95
      max_charge_power = 12.0  # Increased from 10
      max_discharge_power = 6.0  # Increased from 5
    elif current_grid_sell_price > 1.8 * current_grid_buy_price:  # Extremely high sell price
      target_soc_min = 0.05  # Prepare to discharge
      target_soc_max = 0.30
      max_charge_power = 3.0
      max_discharge_power = 12.0  # Asymmetrically favor discharge
    else:
      # Balanced mode with moderate thresholds
      target_soc_min = 0.20
      target_soc_max = 0.85
      max_charge_power = 10.0
      max_discharge_power = 8.0

    action_kw = 0.0

    # STRATEGY 1: Capture arbitrage spread aggressively
    if price_spread > 0.15 and current_soc > 0.25:  # Profitable to discharge
      discharge_power = min(abs(net_power) if net_power < 0 else 2.0,
                          max_discharge_power,
                          current_energy_stored_kwh)
      action_kw = -discharge_power

    # STRATEGY 2: Aggressive charging at low prices
    elif current_grid_buy_price < 0.60 * current_grid_sell_price and current_soc < target_soc_max:
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      charge_power = min(max_charge_power, available_capacity)
      action_kw = charge_power

    # STRATEGY 3: PV-driven optimization with firming
    elif net_power > 0.5 and current_soc < 0.90:  # Excess PV generation
      excess = net_power
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(excess, max_charge_power, available_capacity)

    elif net_power < -0.5 and current_soc > target_soc_min:  # Demand deficit
      deficit = -net_power
      action_kw = -min(deficit, max_discharge_power, current_energy_stored_kwh)

    # STRATEGY 4: Opportunistic pre-charging for predicted low prices (simple heuristic)
    elif action_kw == 0 and current_soc < 0.50 and current_grid_buy_price < 0.70:
      available_capacity = battery_capacity_kwh - current_energy_stored_kwh
      action_kw = min(8.0, available_capacity)

    # Final safety checks
    max_possible_charge = battery_capacity_kwh - current_energy_stored_kwh
    max_possible_discharge = current_energy_stored_kwh

    if action_kw > 0:
      action_kw = min(action_kw, max_possible_charge)
    else:
      action_kw = max(action_kw, -max_possible_discharge)

    return action_kw
