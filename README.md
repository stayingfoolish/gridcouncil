# gridcouncil — Grid Optimization Engine

A multi-agent optimization engine for the power grid: a **digital twin**
calibrated on real market data, an **optimizer** that dispatches the
flexibility new loads already have, and an **agentic policy search** loop in
which AI agents write, test, and iteratively improve control strategies as
ordinary, auditable code.

Electricity prices are climbing as data centers and EV adoption pile new
demand onto the grid. Because wholesale markets clear at the marginal price,
a single new demand peak can raise bills for everyone. The engine answers the
question every city, utility, and operator is asking: **how much new load can
the grid welcome before prices spike — and what do we do about it?**

## Demo app

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python numpy scipy matplotlib pandas scikit-learn pyarrow gridstatus streamlit anthropic
.venv/bin/python -m streamlit run app.py
```

Seven tabs: the problem on real market data (including a zonal price map),
two precomputed stories (a home battery in €, a 500 MW data center in $),
how the agents talk to each other, two Live Labs that run the agentic search
in real time, and a full-transparency tab (prompts, data, logs, verbatim
transcripts).

## Layout

```
engine/
  data.py           day-ahead LMP + load + fuel mix via gridstatus (NYISO open
                    feeds; PJM adapter slot pending an API key)
  twin.py           merit-order digital twin: isotonic supply stack + hourly
                    adjustment, out-of-sample calibration report
  scenario.py       load injection -> counterfactual prices & bill impact
  optimizer.py      flexibility dispatch: threshold rules (PFA) vs lookahead LP
                    (DLA) over the convex stack cost, arbitrated by realized cost
  aps_dispatch.py   environment + prompts for LLM-written dispatch policies
  aps_episode.py    episode state machine for the data-center policy search
aps/
  simulation.py     simulated home: PV, battery, dynamic tariff (one week)
  benchmark.py      perfect-foresight LP optimum (finite-horizon & steady-state)
  prompts.py        prompt templates for the home-battery policy search
  episode.py        episode state machine: generation, 5-attempt repair loop,
                    greedy refine/explore meta-policy with stagnation switching
  policy_runtime.py post-processing + sandboxed subprocess evaluation
experiments/
  driver.py         filesystem-based search driver, home battery
  driver2.py        filesystem-based search driver, data-center dispatch
  worker.py         LLM worker loop (Anthropic API or claude CLI)
  analyze.py        aggregation + summary figures
  run_engine_demo.py end-to-end scenario demo + figures + explainer
results/            recorded runs: per-round strategies, scores, event logs,
                    verbatim transcripts
```

## How the agentic search works

Three specialists in a loop, communicating through files on disk (every
message is archived, so every run is replayable):

1. A **coach** (meta-level LLM) sees only aggregate scores — never raw market
   data — and issues one instruction per round: refine the current strategy,
   or rethink it entirely (automatic switch on stagnation).
2. A **strategy writer** (code-generating LLM) turns the instruction into a
   complete policy class — plain Python, auditable line by line.
3. A **grid twin / simulator** (not an AI) scores the policy against the data.
   Crashes summon a **bug fixer** that repairs the code from the error message
   (up to 5 attempts, then the round restarts).

The loop always keeps the best strategy found. Classical optimizers (the
lookahead LP) stay in the arbitration as both a competitor and a
provable bound, and the scoreboard picks the winner.

## Headline results (all reproducible from this repo)

- **Digital twin**: out-of-sample correlation 0.88, MAE $20.7/MWh on 75 days
  of NYISO day-ahead prices; held-out weeks used for every number shown.
- **Scenario**: a rigid 500 MW data center raises the peak clearing price by
  +$196/MWh (+$76M on consumer bills over 2 weeks); dispatching its own
  flexibility cuts the consumer impact 33% and its bill 22%; the computed
  storage-sizing answer (250 MW / 1 GWh) brings the peak impact to +$56/MWh.
- **Home battery search**: 10/10 independent searches found a profitable
  strategy within 3 rounds; best within 0.24 € of the perfect-foresight
  optimum for the simulated week.
- **Data-center dispatch search**: LLM-written policies closed 27% of the
  naive-to-optimal gap vs 55% for hand-written rules — the honest finding
  that motivates keeping the classical optimizer in the loop.
