# gridcouncil — APS Replication

Replication of **"Adaptive Self-Improvement for Smarter Energy Systems using
Agentic Policy Search"** (Sommer, Bazan, Babaeian, Fellerer, Powell, German;
FAU Erlangen-Nürnberg / Princeton).

The paper proposes **Agentic Policy Search (APS)**: a three-level hierarchy in
which an LLM writes executable battery-control policies for a residential
energy system (Level 2), a meta-level LLM iteratively refines or replaces the
task instructions based on simulation feedback (Level 3), and the policies are
evaluated in a simulated 7-day residential energy system (Level 1).

## Layout

```
aps/
  simulation.py     Level 1: exogenous processes + battery simulation (Sec. 7.1, Table 1)
  benchmark.py      Perfect-foresight LP optimum (Appendix D), finite-horizon & steady-state
  prompts.py        Verbatim prompt templates (Sec. 7.2/7.3, Appendix C)
  policy_runtime.py Post-processing Phi + sandboxed subprocess evaluation (Sec. 7.1.7)
  episode.py        Algorithm 1 state machine: generation, 5-attempt repair loop,
                    greedy refine/explore meta-policy with stagnation switching
experiments/
  driver.py         Filesystem-based experiment driver (init / step / status)
  analyze.py        Aggregation + replication of Figures 2, 3, 4, 5
results/run1/       Experiment data: per-episode states, generated policies, figures
```

## Reproduction

```bash
uv venv --python 3.11 .venv && uv pip install numpy scipy matplotlib
.venv/bin/python experiments/driver.py init --run results/run1 --episodes 10 --iterations 10
# Loop: serve each episode_*/pending_prompt.txt with an LLM, write response.txt, then
.venv/bin/python experiments/driver.py step --run results/run1
# When DONE: aggregate + figures
.venv/bin/python experiments/analyze.py results/run1
```

The driver is LLM-agnostic: any worker that reads `pending_prompt.txt` and
writes `response.txt` can serve completions. In this replication each
completion was served by a fresh, stateless **Claude Haiku 4.5** call (the
paper used Gemini 2.5 Flash Preview via OpenRouter; both are the
"cheap fast model" tier).

## Benchmark calibration (deterministic part)

The paper's exogenous noise processes are not fully specified, so the free
shape parameters (peak widths, PV irradiance factor, price noise) were
calibrated against the paper's three reference values with seed 42:

| Quantity            | Paper   | This repo |
|---------------------|---------|-----------|
| No-battery cost     | 10.70 € | 10.64 €   |
| Finite-horizon opt. | −6.67 € | −6.32 €   |
| Steady-state opt.   | −5.20 € | −5.55 €   |
| Price volatility    | 10 %    | 10.1 %    |

## Deviations from the paper

- 10 episodes instead of 20 (compute budget); 10 iterations each, as in the paper.
- LLM: Claude Haiku 4.5 (stateless subagent calls) instead of Gemini 2.5 Flash.
- Iteration restarts capped at 2 (after 5 failed repairs each) before a NaN
  iteration is recorded; the paper restarts unboundedly.
- Exogenous noise processes reconstructed by calibration (see above).

See `results/run1/figures/` and the replication report for findings.

## Grid Optimization Engine (cost mode v1)

`engine/` implements the cost-mode v1 of the Grid Optimization Engine on real
ISO data (NYISO open feeds; PJM adapter stub pending an API key):

```
engine/data.py       day-ahead LMP + load + fuel mix via gridstatus (cached)
engine/twin.py       merit-order twin: isotonic supply stack + hourly adjustment,
                     out-of-sample calibration report (the credibility anchor)
engine/scenario.py   load injection -> counterfactual prices & bill impact
engine/optimizer.py  flexibility dispatch: PFA threshold rules vs DLA lookahead LP
                     (convex stack cost), arbitrated by realized system cost
experiments/run_engine_demo.py  end-to-end demo + figures + plain-English explainer
```

Demo result (NYISO, 2.1 held-out weeks, twin corr 0.88): a 500 MW inflexible
data center raises the peak clearing price +196 $/MWh and consumer bills
+76.3 M$; dispatching its own flexibility (50 % deferrable compute, storage)
cuts the consumer impact 33 % and its energy bill 22 %; the engine's sizing
answer (250 MW / 1 GWh storage) brings the peak impact down to +56 $/MWh.
Outputs in `results/engine_demo/`.
