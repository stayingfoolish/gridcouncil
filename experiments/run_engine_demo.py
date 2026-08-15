"""End-to-end demo of the Grid Optimization Engine cost mode.

Calibrate the twin on NYISO history, inject a 500 MW data center over the
held-out test weeks, price the counterfactual, dispatch its flexibility with
both policy classes, and emit figures + a plain-English explainer.
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.data import fetch
from engine.optimizer import arbitrate
from engine.scenario import assess, datacenter
from engine.twin import MeritOrderTwin

OUT = Path("results/engine_demo")
OUT.mkdir(parents=True, exist_ok=True)

df, iso = fetch("2026-06-01", "2026-08-14")
df = df.sort_values("time").reset_index(drop=True)
twin = MeritOrderTwin.calibrate(df)
rep = twin.report
n_test = rep.n_test
test = df.iloc[-n_test:].reset_index(drop=True)

# ---- scenario: 500 MW data center on the held-out weeks ----
dc = datacenter(len(test), mw=500.0, deferrable_frac=0.5,
                battery_mwh=400.0, battery_mw=100.0)
impact = assess(twin, test, dc)
results = arbitrate(twin, test, dc)
naive, pfa, dla, best = results["naive"], results["pfa"], results["dla"], results["best"]
mitigated = assess(twin, test, dc, injected_mw=best.served_mw)

# ---- sizing answer: storage that fully covers the firm load at the peak ----
dc_sized = datacenter(len(test), mw=500.0, deferrable_frac=0.5,
                      battery_mwh=1000.0, battery_mw=250.0)
sized = arbitrate(twin, test, dc_sized)["best"]
sized_impact = assess(twin, test, dc_sized, injected_mw=sized.served_mw)

# ---- figure 1: calibration ----
pred_all = twin.predict(df["net_load_mw"].values, df["time"])
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4), width_ratios=[2, 1])
ax1.plot(test["time"], test["LMP"], color="0.3", lw=1.0, label="Observed DA LMP")
ax1.plot(test["time"], pred_all[-n_test:], color="tab:red", lw=1.0, alpha=0.85,
         label="Twin (out-of-sample)")
ax1.set_ylabel("$/MWh"); ax1.legend(fontsize=8)
ax1.set_title(f"Held-out fit: MAE ${rep.mae:.1f}/MWh, corr {rep.corr:.2f}")
order = np.argsort(df["net_load_mw"].values)
ax2.scatter(df["net_load_mw"] / 1e3, df["LMP"], s=3, alpha=0.15, color="0.4")
ax2.plot(twin.grid / 1e3, twin.grid_price, color="tab:red", lw=2)
ax2.set_xlabel("Net load [GW]"); ax2.set_ylabel("$/MWh")
ax2.set_ylim(0, np.percentile(df["LMP"], 99.5))
ax2.set_title("Reconstructed merit-order stack")
fig.autofmt_xdate(); fig.tight_layout()
fig.savefig(OUT / "fig1_calibration.png", dpi=160)

# ---- figure 2: counterfactual vs mitigated prices (worst week) ----
p0, p1 = impact.price_base, impact.price_new
worst = int(np.argmax(p1 - p0)); a, b = max(0, worst - 84), min(len(test), worst + 84)
sl = slice(a, b)
fig, ax = plt.subplots(figsize=(11, 4))
ax.plot(test["time"][sl], p0[sl], color="0.35", lw=1.1, label="Baseline")
ax.plot(test["time"][sl], p1[sl], color="tab:red", lw=1.1,
        label=f"+500 MW DC, inflexible (peak +${impact.peak_price_delta:.0f})")
ax.plot(test["time"][sl], mitigated.price_new[sl], color="tab:green", lw=1.2,
        label=f"+500 MW DC, optimized ({best.policy}, peak +${mitigated.peak_price_delta:.0f})")
ax.set_ylabel("$/MWh"); ax.legend(fontsize=8)
ax.set_title("Counterfactual day-ahead prices around the worst hour")
fig.autofmt_xdate(); fig.tight_layout()
fig.savefig(OUT / "fig2_counterfactual.png", dpi=160)

# ---- figure 3: dispatch detail ----
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True)
ax1.plot(test["time"][sl], naive.served_mw[sl], color="tab:red", lw=1, label="Inflexible draw")
ax1.plot(test["time"][sl], best.served_mw[sl], color="tab:green", lw=1.2, label="Optimized draw")
ax1.set_ylabel("MW"); ax1.legend(fontsize=8)
ax1.set_title("Data-center grid draw: flexibility follows the stack")
ax2.plot(test["time"][sl], p0[sl], color="0.5", lw=0.9, label="Baseline price")
ax2b = ax2.twinx()
ax2b.fill_between(test["time"][sl], best.battery_mw[sl], 0, color="tab:blue",
                  alpha=0.4, label="Battery MW")
ax2.set_ylabel("$/MWh"); ax2b.set_ylabel("Battery MW")
ax2.legend(fontsize=8, loc="upper left"); ax2b.legend(fontsize=8, loc="upper right")
fig.autofmt_xdate(); fig.tight_layout()
fig.savefig(OUT / "fig3_dispatch.png", dpi=160)

# ---- summary + explainer ----
weeks = n_test / (24 * 7)
def row(r):
    return {"policy": r.policy,
            "system_cost_delta_usd": round(r.system_cost_delta),
            "dc_energy_cost_usd": round(r.energy_cost),
            "peak_price_delta_usd_mwh": round(r.peak_price_delta, 2)}
summary = {
    "iso": iso, "period": [str(test['time'].iloc[0]), str(test['time'].iloc[-1])],
    "calibration": {"mae": round(rep.mae, 2), "rmse": round(rep.rmse, 2),
                    "mape_pct": round(rep.mape, 1), "corr": round(rep.corr, 3),
                    "peak_decile_mae": round(rep.peak_mae, 2)},
    "scenario": {"new_load_mw": 500, "deferrable_frac": 0.5,
                 "battery": "400 MWh / 100 MW",
                 "consumer_bill_delta_usd": round(impact.consumer_bill_delta),
                 "avg_price_delta_usd_mwh": round(impact.avg_price_delta, 2),
                 "peak_price_delta_usd_mwh": round(impact.peak_price_delta, 2)},
    "mitigated": {"consumer_bill_delta_usd": round(mitigated.consumer_bill_delta),
                  "avg_price_delta_usd_mwh": round(mitigated.avg_price_delta, 2),
                  "peak_price_delta_usd_mwh": round(mitigated.peak_price_delta, 2)},
    "policies": [row(naive), row(pfa), row(dla)],
    "chosen": best.policy,
    "sizing_answer": {
        "battery": "1000 MWh / 250 MW (covers firm load at peak)",
        "peak_price_delta_usd_mwh": round(sized_impact.peak_price_delta, 2),
        "consumer_bill_delta_usd": round(sized_impact.consumer_bill_delta),
    },
}
(OUT / "summary.json").write_text(json.dumps(summary, indent=2))

dc_saving = naive.energy_cost - best.energy_cost
sys_saving = naive.system_cost_delta - best.system_cost_delta
bill_saving = impact.consumer_bill_delta - mitigated.consumer_bill_delta
explainer = f"""ENGINE EXPLAINER — {iso}, {weeks:.1f} held-out weeks

A 500 MW data center connecting as inflexible load would have raised the
market's peak clearing price by ${impact.peak_price_delta:.0f}/MWh and added
${impact.consumer_bill_delta/1e6:.1f}M to existing consumers' bills over the period.

The engine dispatched the flexibility the load already has — 50% deferrable
compute (24h window) and a 400 MWh battery — comparing threshold rules (PFA)
against a lookahead LP (DLA) on the calibrated twin. {best.policy} won the
arbitration. The lever: hold deferrable jobs and discharge storage through
stack-climbing hours, recover overnight when the marginal unit is cheap.

Result: the data center's own energy bill falls ${dc_saving/1e6:.2f}M
({dc_saving/naive.energy_cost*100:.0f}%), added system dispatch cost falls
${sys_saving/1e6:.2f}M ({sys_saving/naive.system_cost_delta*100:.0f}%), and the
consumer bill impact drops ${bill_saving/1e6:.2f}M
({bill_saving/max(impact.consumer_bill_delta,1)*100:.0f}%). Peak price impact:
+${impact.peak_price_delta:.0f} -> +${mitigated.peak_price_delta:.0f}/MWh.

The residual peak impact comes from the 250 MW firm floor: at the scarcity
hour the battery (100 MW) cannot cover it. The engine's sizing answer: with
250 MW / 1000 MWh of storage the peak impact falls to
+${sized_impact.peak_price_delta:.0f}/MWh and the consumer bill impact to
${sized_impact.consumer_bill_delta/1e6:.1f}M — that is the interconnection
package to bring to the operator.
"""
(OUT / "explainer.txt").write_text(explainer)
print(explainer)
print(json.dumps(summary["policies"], indent=1))
