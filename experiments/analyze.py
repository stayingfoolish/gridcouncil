"""Aggregate APS run results and replicate the paper's figures.

Outputs (into <run>/figures/):
  fig2_cost_evolution.png   - median/IQR/min-max cost per iteration + benchmarks
  fig3_best_policy.png      - SoC + control actions of the best policy found
  fig45_exogenous.png       - demand/generation and price time series
  summary.json              - aggregate statistics vs. paper values
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aps.benchmark import solve_optimal
from aps.simulation import NoBatteryPolicy, SystemParams, generate_exogenous, simulate

PAPER = {"fh_opt": -6.67, "ss_opt": -5.20, "no_battery": 10.70}


def load_run(run_dir: Path):
    cfg = json.loads((run_dir / "config.json").read_text())
    episodes = []
    for ep_dir in sorted(run_dir.glob("episode_*")):
        st = json.loads((ep_dir / "state.json").read_text())
        episodes.append({"dir": ep_dir, "state": st})
    return cfg, episodes


def main(run_dir: Path, drop_outliers: bool = True):
    cfg, episodes = load_run(run_dir)
    params = SystemParams(**cfg["params"])
    series = generate_exogenous(params)
    fig_dir = run_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    nb = simulate(NoBatteryPolicy(), series, params).total_cost
    fh = solve_optimal(series, params).total_cost
    ss = solve_optimal(series, params, steady_state=True).total_cost

    n_iter = cfg["iterations"]
    cost = np.full((len(episodes), n_iter), np.nan)
    for i, ep in enumerate(episodes):
        for rec in ep["state"]["records"]:
            cost[i, rec["iteration"]] = rec["total_cost"]

    # Paper footnote 3: single-iteration outliers removed in post-processing.
    outliers = []
    cost_f = cost.copy()
    if drop_outliers:
        med, q3 = np.nanmedian(cost), np.nanpercentile(cost, 75)
        thresh = q3 + 3 * (q3 - np.nanpercentile(cost, 25))
        for i in range(cost.shape[0]):
            for j in range(cost.shape[1]):
                if np.isfinite(cost[i, j]) and cost[i, j] > thresh:
                    outliers.append((i + 1, j + 1, float(cost[i, j])))
                    cost_f[i, j] = np.nan

    it = np.arange(1, n_iter + 1)
    median = np.nanmedian(cost_f, axis=0)
    q1 = np.nanpercentile(cost_f, 25, axis=0)
    q3 = np.nanpercentile(cost_f, 75, axis=0)
    lo = np.nanmin(cost_f, axis=0)
    hi = np.nanmax(cost_f, axis=0)

    # ---- Figure 2 ----
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.fill_between(it, lo, hi, color="0.8", label="Min-Max")
    ax.fill_between(it, q1, q3, color="lightgreen", alpha=0.8, label="Q1-Q3")
    ax.plot(it, median, "k-o", ms=4, label="Median")
    ax.axhline(fh, color="green", ls="--", label=f"FH-Opt. ({fh:.2f})")
    ax.axhline(ss, color="blue", ls="-.", label=f"SS-Opt. ({ss:.2f})")
    ax.axhline(nb, color="red", ls="--", label=f"No Battery ({nb:.2f})")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Cost [EUR]")
    ax.set_title("Evolution of cumulative cost over iterations (Fig. 2 replication)")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.set_xticks(it)
    fig.tight_layout()
    fig.savefig(fig_dir / "fig2_cost_evolution.png", dpi=160)

    # ---- Figure 3: best policy overall ----
    best = None
    for ep in episodes:
        trace_path = ep["dir"] / "best_trace.json"
        if trace_path.exists():
            trace = json.loads(trace_path.read_text())
            if best is None or trace["total_cost"] < best[0]:
                best = (trace["total_cost"], ep["dir"].name, trace)
    if best:
        _, ep_name, trace = best
        soc = np.array(trace["soc_kwh"])
        act = np.array(trace["actions_kw"])
        t = np.arange(len(act))
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
        ax1.plot(t, soc[:-1], "-", color="tab:blue")
        ax1.set_ylabel("State of charge [kWh]")
        ax1.set_title(
            f"Best policy ({ep_name}, iter {trace['iteration']}, "
            f"cost {trace['total_cost']:.2f} EUR) - Fig. 3 replication"
        )
        ax2.step(t, act, where="post", color="k", lw=0.8)
        ax2.fill_between(t, act, 0, where=act > 0, step="post", color="lightcoral", label="Buy")
        ax2.fill_between(t, act, 0, where=act < 0, step="post", color="lightgreen", label="Sell")
        ax2.set_ylabel("Action [kW]")
        ax2.set_xlabel("Time [h]")
        ax2.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(fig_dir / "fig3_best_policy.png", dpi=160)

    # ---- Figures 4/5: exogenous series ----
    t = np.arange(params.horizon)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5))
    ax1.plot(t, series.load_kw, color="tab:green", label="Demand")
    ax1.plot(t, series.pv_kw, color="tab:purple", alpha=0.8, label="Generation")
    ax1.set_ylabel("Power [kW]")
    ax1.legend(fontsize=8)
    ax1.set_title("Demand and on-site generation (Fig. 4) / prices (Fig. 5)")
    ax2.plot(t, series.buy_price, color="tab:blue", label="Buy price")
    ax2.plot(t, series.sell_price, color="tab:red", label=f"Sell price ({params.feed_in_tariff} EUR/kWh)")
    ax2.set_ylabel("Price [EUR/kWh]")
    ax2.set_xlabel("Time [h]")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(fig_dir / "fig45_exogenous.png", dpi=160)

    summary = {
        "benchmarks": {
            "no_battery": {"ours": round(nb, 2), "paper": PAPER["no_battery"]},
            "finite_horizon_opt": {"ours": round(fh, 2), "paper": PAPER["fh_opt"]},
            "steady_state_opt": {"ours": round(ss, 2), "paper": PAPER["ss_opt"]},
            "price_volatility_pct": round(series.price_volatility_pct, 1),
        },
        "episodes": len(episodes),
        "iterations": n_iter,
        "outliers_removed": outliers,
        "median_per_iteration": [round(float(m), 2) for m in median],
        "best_cost_per_episode": {
            ep["dir"].name: round(min(
                (r["total_cost"] for r in ep["state"]["records"]
                 if np.isfinite(r["total_cost"])), default=float("nan")), 2)
            for ep in episodes
        },
        "best_overall": {
            "cost": round(best[0], 2) if best else None,
            "episode": best[1] if best else None,
            "iteration": best[2]["iteration"] if best else None,
            "gap_to_fh_opt": round(best[0] - fh, 2) if best else None,
        },
        "repair_stats": {
            "total_repairs": int(sum(r["repair_attempts"] for ep in episodes
                                     for r in ep["state"]["records"])),
            "failed_iterations": int(sum(1 for ep in episodes
                                         for r in ep["state"]["records"]
                                         if not np.isfinite(r["total_cost"]))),
        },
    }
    (fig_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "results/run1"))
