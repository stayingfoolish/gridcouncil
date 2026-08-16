"""Prompt templates for the strategy-generation level and the meta (coach)
level, with placeholder variables filled per iteration.
"""

POLICY_SIGNATURE = '''class Policy:
  def __init__(self):
    """Initializes the policy.
    Parameters or internal states can be defined here
    """
    pass

  def take_action(self,
    # energy stored in the battery [kWh]
    current_energy_stored_kwh: float,
    # PV power generation [kW]
    current_pv_generation_kw: float,
    # household power demand [kW]
    current_demand_kw: float,
    # grid purchase price [euro/kWh]
    current_grid_buy_price: float,
    # grid feed-in tariff (sell price) [euro/kWh]
    current_grid_sell_price: float,
    # Maximum battery capacity [kWh]
    battery_capacity_kwh: float,
  ) -> float:
    """Determines the target action for the battery based on the current state.

    Returns:
      float: The target power for the battery [kW]
        positive: charging; negative: discharging;
        zero: no action
    """
    # --- Implement your logic here ---
    # Example: Always return 0 (no action)
    action_kw = 0.0

    # Return the calculated action
    return action_kw'''


GENERATION_PROMPT = '''You are an expert Python developer.

Develop an intelligent battery management policy to optimize energy costs while satisfying the demand_sequence.
The policy must make strategic decisions about:
1. When to charge the battery (buy & store energy)
2. When to discharge the battery (sell & discharge energy)
3. When to directly purchase from the market

Key Constraints:
1. Battery Capacity:
   - 0 <= energy_stored <= max_energy_stored
   - Battery charge must stay within physical limits
   - power_discharge >= -5
   - power_charge <= 10

2. Energy Conservation:
   - energy_discharged <= energy_stored
   - Cannot discharge more energy than stored

3. Demand Coverage:
   - power_bought + power_discharged >= power_own_demand
   - Must meet energy demand_sequence in each timestep

Structure example:
{policy_signature}

Implementation instructions:
{task_description}

Provide the final implementation without Markdown formatting or additional comments outside the class.'''


REPAIR_PROMPT = '''You are an expert Python developer debugging a battery management system implementation.

A BatteryPolicy implementation has failed in the simulation environment with the following:
Error Message:
{error_message}

Failed Code:
```python
{policy_code}
```

Task:
Fix the implementation errors while maintaining the original strategy where appropriate.

Expected output structure:
{policy_signature}

Return only the corrected Policy class implementation without markdown formatting or extra comments outside the class.'''


META_PROMPT = '''You are an expert developing an intelligent battery management system.
Current Total Cost: {total_cost}
Best Cost Achieved: {best_cost}
Iteration: {iteration_count}
Battery Utilization: {utilization}%
Average State of Charge: {avg_soc}
Price Volatility: {price_volatility}
Performance History (last 5 costs): {cost_history}

Current Implementation:
```python
{policy_code}
```

{explore_or_refine_instruction}

Your task:
1. Analyze the current implementation's strengths and limitations
2. {task_mode}
3. Provide specific parameter values and implementation details
4. Explain expected impact on cost and system behavior

Focus on CONCRETE improvements that can be implemented immediately.'''


REFINE_INSTRUCTION = "Suggest ONE specific improvement to the existing approach"
EXPLORE_INSTRUCTION = (
    "Propose a novel approach that fundamentally rethinks how we make "
    "charging/discharging decisions"
)
REFINE_TASK_MODE = (
    "The current approach shows potential. Focus on targeted improvements "
    "while maintaining core strategy."
)
EXPLORE_TASK_MODE = (
    "The current approach shows stagnation. Consider a fundamentally "
    "different strategy to optimize the battery management system."
)

INITIAL_TASK_DESCRIPTION = (
    "Implement a rule-based battery management policy that decides, based on "
    "the current system state, when to charge the battery, when to discharge "
    "it, and when to purchase energy directly from the market, with the goal "
    "of minimizing the total energy cost."
)
