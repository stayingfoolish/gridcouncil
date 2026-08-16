"""APS-over-engine: LLM-generated dispatch policies for data-center
flexibility, searched with the replication's meta-policy machinery and
benchmarked against the DLA LP bound (optimal for the convex stack).

The generated policy controls, hour by hour, how much deferrable compute to
serve and how to move the battery, given the current price and local state.
The environment enforces physical limits and the 24 h compute deadline by
clipping/forcing, exactly as the APS paper's simulation clips infeasible
actions.
"""

import json
import pickle
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------- prompts

POLICY_SIGNATURE = '''class DispatchPolicy:
  def __init__(self):
    """Initializes the policy. Internal state (price history, thresholds,
    counters) can be kept here across time steps."""
    pass

  def take_action(self,
    # hour of day 0-23
    hour_of_day: int,
    # baseline day-ahead price this hour [$/MWh]
    current_price: float,
    # inflexible data-center load this hour [MW]
    firm_load_mw: float,
    # newly arriving deferrable compute this hour [MW]
    arriving_flex_mw: float,
    # deferred compute waiting to be served [MWh]
    backlog_mwh: float,
    # age of the oldest deferred compute [hours] (deadline: 24)
    oldest_backlog_age_h: float,
    # battery state of charge [MWh]
    battery_soc_mwh: float,
    # battery capacity [MWh]
    battery_capacity_mwh: float,
    # battery power limit [MW]
    battery_power_mw: float,
  ) -> tuple:
    """Decide this hour's flexible dispatch.

    Returns:
      (flex_serve_mw, battery_mw)
      flex_serve_mw: deferrable compute to run now [MW] (0 = defer everything;
        serving more than arriving_flex_mw works down the backlog)
      battery_mw: positive = charge, negative = discharge [MW]
    """
    # --- Implement your logic here ---
    return arriving_flex_mw, 0.0'''


GENERATION_PROMPT = '''You are an expert Python developer working on power-market optimization.

Develop an intelligent dispatch policy for a 500 MW data center connected to
a wholesale electricity market. Half of its load (250 MW arriving each hour)
is deferrable compute that may wait up to 24 hours; the other 250 MW is firm.
It also has a battery (400 MWh, 100 MW, ~88% round-trip efficiency).
Electricity prices follow a daily cycle (roughly $30-100/MWh normally) with
occasional scarcity spikes into the hundreds of dollars. The grid price rises
when total load rises, so concentrating load into already-expensive hours is
doubly costly.

The policy must decide each hour:
1. How much deferrable compute to serve now vs defer (backlog served later)
2. When to charge and discharge the battery

Objective: minimize the total added system dispatch cost (equivalently: avoid
drawing power in expensive hours, shift work and storage to cheap hours).

Key constraints (enforced by the environment, but respect them):
1. Deferred compute must be served within 24 hours (the environment
   force-serves overdue backlog at any price - avoid letting this happen)
2. 0 <= battery state of charge <= capacity; |battery power| <= limit
3. flex_serve_mw >= 0; serving beyond arrivals draws down the backlog

Structure example:
{policy_signature}

Implementation instructions:
{task_description}

Provide the final implementation without Markdown formatting or additional comments outside the class.'''


REPAIR_PROMPT = '''You are an expert Python developer debugging a power-market dispatch policy.

A DispatchPolicy implementation has failed in the simulation environment with the following:
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

Return only the corrected DispatchPolicy class implementation without markdown formatting or extra comments outside the class.'''


META_PROMPT = '''You are an expert developing a dispatch optimizer for a data center in a wholesale power market.
Current Added System Cost: ${total_cost}
Best (lowest) Added System Cost Achieved: ${best_cost}
Theoretical optimum (perfect-foresight LP): ${lp_bound}
Naive inflexible baseline: ${naive_cost}
Iteration: {iteration_count}
Data-center Energy Bill: ${energy_cost}
Peak Price Impact: {peak_delta} $/MWh
Force-served Overdue Compute: {forced_mwh} MWh (deadline violations recovered by the environment)
Battery Utilization: {battery_util}%
Performance History (last 5 added system costs): {cost_history}

Current Implementation:
```python
{policy_code}
```

{explore_or_refine_instruction}

Your task:
1. Analyze the current implementation's strengths and limitations
2. {task_mode}
3. Provide specific parameter values and implementation details
4. Explain expected impact on cost and dispatch behavior

Focus on CONCRETE improvements that can be implemented immediately.'''


REFINE_INSTRUCTION = "Suggest ONE specific improvement to the existing approach"
EXPLORE_INSTRUCTION = ("Propose a novel approach that fundamentally rethinks how "
                       "we schedule deferrable compute and battery dispatch")
REFINE_TASK_MODE = ("The current approach shows potential. Focus on targeted "
                    "improvements while maintaining core strategy.")
EXPLORE_TASK_MODE = ("The current approach shows stagnation. Consider a "
                     "fundamentally different strategy for the dispatch problem.")
INITIAL_TASK_DESCRIPTION = (
    "Implement a rule-based dispatch policy that decides, from the current "
    "price and local state, when to run deferrable compute, when to defer it, "
    "and when to charge or discharge the battery, minimizing the added system "
    "dispatch cost of the data center."
)

# ----------------------------------------------------------------- environment

DEADLINE_H = 24
MAX_SERVE_MW = 1500.0


@dataclass
class DispatchOutcome:
    ok: bool
    error: str = ""
    system_cost_delta: float = float("nan")
    energy_cost: float = float("nan")
    peak_price_delta: float = float("nan")
    forced_mwh: float = 0.0
    battery_util_pct: float = 0.0
    served_mw: list = None
    battery_mw: list = None
    backlog_mwh: list = None


def simulate_dispatch(policy, env: dict) -> DispatchOutcome:
    """Run a generated DispatchPolicy over the test window against the twin.

    env: dict with base_net (np.ndarray), p0 (baseline prices), hours,
    stack_x, stack_price, firm_mw, flex_mw, batt_mwh, batt_mw, eta.
    """
    base_net = env["base_net"]; p0 = env["p0"]; hours = env["hours"]
    T = len(base_net)
    firm, flex = env["firm_mw"], env["flex_mw"]
    E, P, eta = env["batt_mwh"], env["batt_mw"], env["eta"]

    soc = E / 2.0
    backlog = deque()   # (mwh, age_h)
    served = np.zeros(T); batt = np.zeros(T); blog = np.zeros(T)
    forced_total = 0.0

    for t in range(T):
        backlog_mwh = sum(a for a, _ in backlog)
        oldest = max((age for _, age in backlog), default=0.0)
        raw = policy.take_action(
            hour_of_day=int(hours[t]),
            current_price=float(p0[t]),
            firm_load_mw=firm,
            arriving_flex_mw=flex,
            backlog_mwh=float(backlog_mwh),
            oldest_backlog_age_h=float(oldest),
            battery_soc_mwh=float(soc),
            battery_capacity_mwh=E,
            battery_power_mw=P,
        )
        s_req, b_req = float(raw[0]), float(raw[1])
        if not (np.isfinite(s_req) and np.isfinite(b_req)):
            raise ValueError(f"non-finite action at t={t}: {raw!r}")

        # deferrable accounting: arrivals join the queue, then serve requests
        backlog.append([flex * 1.0, 0.0])
        s = float(np.clip(s_req, 0.0, min(backlog_mwh + flex, MAX_SERVE_MW)))
        # deadline enforcement: force-serve anything at the deadline
        forced = sum(a for a, age in backlog if age >= DEADLINE_H - 1)
        s = max(s, forced)
        forced_total += max(0.0, min(forced, forced))
        remaining = s
        while remaining > 1e-9 and backlog:
            amt, age = backlog[0]
            take = min(amt, remaining)
            backlog[0][0] -= take
            remaining -= take
            if backlog[0][0] <= 1e-9:
                backlog.popleft()
        for item in backlog:
            item[1] += 1.0

        # battery clipping (per-leg efficiency)
        x = float(np.clip(b_req, -P, P))
        if x > 0:
            x = min(x, (E - soc) / eta)
        else:
            x = max(x, -soc * eta)
        soc += eta * max(x, 0.0) - max(-x, 0.0) / eta

        served[t] = max(firm + s + x, 0.0)
        batt[t] = x
        blog[t] = sum(a for a, _ in backlog)

    # cost metrics against the stack
    sx, sp = env["stack_x"], env["stack_price"]
    cum = np.concatenate([[0.0], np.cumsum(0.5 * (sp[1:] + sp[:-1]) * np.diff(sx))])
    C = lambda x: np.interp(x, sx, cum)
    delta = float((C(base_net + served) - C(base_net)).sum())
    p1 = np.interp(base_net + served, sx, sp) + (p0 - np.interp(base_net, sx, sp))
    return DispatchOutcome(
        ok=True,
        system_cost_delta=delta,
        energy_cost=float((p1 * served).sum()),
        peak_price_delta=float((p1 - p0).max()),
        forced_mwh=float(forced_total),
        battery_util_pct=float((np.abs(batt) > 1e-3).mean() * 100),
        served_mw=served.tolist(),
        battery_mw=batt.tolist(),
        backlog_mwh=blog.tolist(),
    )


def build_env(run_dir: Path) -> dict:
    """Build and pickle the evaluation environment from the calibrated twin."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine.data import fetch
    from engine.twin import MeritOrderTwin

    df, _ = fetch("2026-06-01", "2026-08-14")
    df = df.sort_values("time").reset_index(drop=True)
    twin = MeritOrderTwin.calibrate(df)
    test = df.iloc[-twin.report.n_test:].reset_index(drop=True)
    env = {
        "base_net": test["net_load_mw"].values,
        "p0": twin.predict(test["net_load_mw"].values, test["time"]),
        "hours": test["time"].dt.hour.values,
        "stack_x": twin.grid, "stack_price": twin.grid_price,
        "firm_mw": 250.0, "flex_mw": 250.0,
        "batt_mwh": 400.0, "batt_mw": 100.0, "eta": float(np.sqrt(0.88)),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "env.pkl", "wb") as f:
        pickle.dump(env, f)
    return env
