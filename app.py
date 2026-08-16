"""Grid Optimization Engine — demo app for a non-technical audience.

    streamlit run app.py

Three-act story:
  1. The problem   — real electricity prices, and why one big new load raises
                     everyone's bill (the marginal-price auction).
  2. The engine    — drop a data center on the grid, watch the counterfactual,
                     then let the engine dispatch its flexibility.
  3. The AI loop   — replay a recorded Agentic Policy Search run: an AI writes
                     a strategy, tests it, reads the score, and rewrites it.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.data import fetch
from engine.optimizer import dispatch_dla, dispatch_pfa, evaluate
from engine.scenario import assess, datacenter
from engine.twin import MeritOrderTwin

st.set_page_config(page_title="Grid Optimization Engine", page_icon="⚡",
                   layout="wide")

EUR_BENCH = {"No battery": 10.64, "Best possible (perfect foresight)": -6.32}
USD_BENCH_KEYS = {"naive": "Do nothing (inflexible)",
                  "pfa": "Hand-written rules",
                  "dla": "Best possible (perfect foresight)"}


@st.cache_resource(show_spinner="Loading real market data and calibrating the twin…")
def load_twin():
    df, iso = fetch("2026-06-01", "2026-08-14")
    df = df.sort_values("time").reset_index(drop=True)
    twin = MeritOrderTwin.calibrate(df)
    test = df.iloc[-twin.report.n_test:].reset_index(drop=True)
    return df, test, twin, iso


@st.cache_data(show_spinner=False)
def run_scenario(mw: float, flex_pct: int, batt_mw: int, use_lookahead: bool):
    _, test, twin, _ = load_twin()
    dc = datacenter(len(test), mw=mw, deferrable_frac=flex_pct / 100,
                    battery_mwh=batt_mw * 4.0, battery_mw=float(batt_mw))
    naive = evaluate(twin, test, dc, dc.profile_mw, "naive")
    impact = assess(twin, test, dc)
    opt = dispatch_dla(twin, test, dc) if use_lookahead else dispatch_pfa(twin, test, dc)
    mitigated = assess(twin, test, dc, injected_mw=opt.served_mw)
    return dc, naive, impact, opt, mitigated


def load_aps_run(run_dir: Path, currency: str):
    episodes = {}
    for d in sorted(run_dir.glob("episode_*")):
        stf = d / "state.json"
        if not stf.exists():
            continue
        state = json.loads(stf.read_text())
        recs = []
        for r in state["records"]:
            cost = r.get("total_cost", r.get("system_cost_delta"))
            code_file = r.get("code_file")
            code = (d / code_file).read_text() if code_file and (d / code_file).exists() else None
            recs.append({"iteration": r["iteration"], "cost": cost,
                         "mode": r.get("mode", "?"), "code": code,
                         "repairs": r.get("repair_attempts", 0)})
        episodes[d.name] = recs
    return episodes


NARRATION = {
    "initial": "🤖 First attempt — the AI writes a strategy from the task description alone.",
    "refine": "✅ The last score improved, so the coach says: **keep this strategy, sharpen one thing**.",
    "explore": "🔄 No progress lately, so the coach says: **throw it out, try something fundamentally different**.",
}

# ================================================================ layout

st.title("⚡ Grid Optimization Engine")
st.caption("A live model of a real power grid, an optimizer that keeps new "
           "demand from raising everyone's bill, and an AI that teaches itself "
           "control strategies. All numbers below come from real market data.")

tab1, tab2, tab3 = st.tabs(["1 · The problem", "2 · The engine", "3 · Watch the AI improve itself"])

# ---------------------------------------------------------------- tab 1
with tab1:
    df, test, twin, iso = load_twin()
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Electricity is an auction — the most expensive power plant sets everyone's price")
        st.markdown(
            f"""Every hour, the grid ({iso}, real data, {len(df)//24} days) buys exactly as much
power as people use. Cheap plants run first; when demand climbs, more expensive
plants switch on — and **that last, most expensive plant sets the price everyone pays**.

That's why one badly-timed gigawatt — a data center, an EV rush hour — can raise
the bill of every home on the grid. The question every city and utility is asking:
**how much new demand can we welcome before prices spike — and what do we do about it?**""")
        m1, m2, m3 = st.columns(3)
        m1.metric("Typical price", f"${df['LMP'].median():.0f}/MWh")
        m2.metric("Worst hour in the data", f"${df['LMP'].max():.0f}/MWh",
                  f"{df['LMP'].max()/df['LMP'].median():.0f}× the typical", delta_color="inverse")
        m3.metric("Twin accuracy (unseen weeks)", f"{twin.report.corr:.0%} correlation")
    with right:
        st.markdown("**Real prices, hour by hour** — spikes are expensive plants switching on")
        chart_df = pd.DataFrame({"time": df["time"], "$/MWh": df["LMP"]}).set_index("time")
        st.line_chart(chart_df, height=260)
    st.info("The engine runs on a **digital twin**: a reconstruction of this auction, "
            "calibrated until its simulated prices track what the market actually did. "
            "Once it tracks reality, it can answer *what-if* questions reality never ran.")

# ---------------------------------------------------------------- tab 2
with tab2:
    st.subheader("Drop a data center on the grid — then let the engine fix the damage")
    c1, c2, c3, c4 = st.columns(4)
    mw = c1.slider("Data center size (MW)", 100, 1000, 500, 50)
    flex_pct = c2.slider("Compute that can wait a day (%)", 0, 80, 50, 10)
    batt_mw = c3.slider("On-site battery (MW)", 0, 300, 100, 50)
    use_lookahead = c4.toggle("Smart planner (looks ahead)", value=True,
                              help="Off = simple price rules. On = the optimizer plans the whole horizon.")

    with st.spinner("Re-running the market with your data center…"):
        dc, naive, impact, opt, mitigated = run_scenario(mw, flex_pct, batt_mw, use_lookahead)

    st.markdown("#### If it connects as a rigid, always-on load:")
    b1, b2, b3 = st.columns(3)
    b1.metric("Peak price impact", f"+${impact.peak_price_delta:.0f}/MWh", delta_color="inverse")
    b2.metric("Extra cost to existing consumers (2 weeks)",
              f"${impact.consumer_bill_delta/1e6:.1f}M", delta_color="inverse")
    b3.metric("Its own power bill", f"${naive.energy_cost/1e6:.1f}M")

    st.markdown("#### After the engine dispatches the flexibility it already has:")
    a1, a2, a3 = st.columns(3)
    a1.metric("Peak price impact", f"+${mitigated.peak_price_delta:.0f}/MWh",
              f"{mitigated.peak_price_delta - impact.peak_price_delta:+.0f}")
    a2.metric("Extra cost to existing consumers",
              f"${mitigated.consumer_bill_delta/1e6:.1f}M",
              f"{(mitigated.consumer_bill_delta - impact.consumer_bill_delta)/1e6:+.1f}M")
    a3.metric("Its own power bill", f"${opt.energy_cost/1e6:.1f}M",
              f"{(opt.energy_cost - naive.energy_cost)/1e6:+.1f}M")

    _, test, twin, _ = load_twin()
    p0, p1 = impact.price_base, impact.price_new
    worst = int(np.argmax(p1 - p0)); a, b = max(0, worst - 60), min(len(test), worst + 60)
    price_df = pd.DataFrame({
        "time": test["time"].iloc[a:b],
        "Before the data center": p0[a:b],
        "Rigid data center": p1[a:b],
        "With the engine": mitigated.price_new[a:b],
    }).set_index("time")
    st.line_chart(price_df, height=280)
    st.success(
        f"**The lever:** hold the {flex_pct}% of compute that can wait, and move the battery, "
        "out of the hours when the expensive plants would switch on — then catch up when power "
        "is cheap. Same computing gets done; it just never sets the price. "
        "The engine also answers the sizing question: more battery flattens the peak impact further.")

# ---------------------------------------------------------------- tab 3
with tab3:
    st.subheader("An AI that writes its own strategy, gets a score, and rewrites itself")
    st.markdown(
        """This is a **recording of a real run** (nothing staged). Each round: an AI writes a
control strategy as code → the twin scores it against the market → a *coach* AI reads the
score and either sharpens the strategy or orders a rethink. Watch the score, and read the
coach's calls.""")

    runs = {}
    if (ROOT / "results/run1").exists():
        runs["Home battery (paper replication) — score in €, lower is better"] = \
            (ROOT / "results/run1", "€", EUR_BENCH)
    if (ROOT / "results/engine_aps").exists():
        bl = json.loads((ROOT / "results/engine_aps/baselines.json").read_text())
        runs["Data-center dispatch (real market) — score in $M added system cost"] = \
            (ROOT / "results/engine_aps", "$M",
             {"Do nothing": bl["naive"]/1e6, "Hand-written rules": bl["pfa"]/1e6,
              "Best possible (perfect foresight)": bl["dla"]/1e6})

    run_name = st.selectbox("Choose a recorded run", list(runs))
    run_dir, unit, bench = runs[run_name]
    episodes = load_aps_run(run_dir, unit)
    ep_name = st.selectbox("Episode (independent attempt)", list(episodes))
    recs = [r for r in episodes[ep_name] if r["cost"] is not None]
    scale = 1e6 if unit == "$M" else 1.0

    max_iter = len(recs)
    shown = st.slider("Play through the rounds ▶", 1, max_iter, 1,
                      help="Drag right to advance the self-improvement loop round by round")

    hist = recs[:shown]
    cur = hist[-1]
    best_so_far = min(h["cost"] for h in hist)

    lc, rc = st.columns([3, 2])
    with lc:
        plot_df = pd.DataFrame({
            "round": [h["iteration"] + 1 for h in hist],
            "this attempt": [h["cost"]/scale for h in hist],
            "best so far": [min(x["cost"] for x in hist[:i+1])/scale for i, h in enumerate(hist)],
        }).set_index("round")
        for label, val in bench.items():
            plot_df[label] = val
        st.line_chart(plot_df, height=320)
    with rc:
        mode = cur["mode"] if shown > 1 else "initial"
        st.markdown(f"### Round {cur['iteration'] + 1}")
        st.markdown(NARRATION.get(mode, ""))
        if cur["repairs"]:
            st.warning(f"🔧 The strategy crashed {cur['repairs']} time(s); the AI read the "
                       "error and repaired its own code before this score.")
        st.metric("This attempt's score", f"{cur['cost']/scale:,.2f} {unit}")
        st.metric("Best so far", f"{best_so_far/scale:,.2f} {unit}",
                  help="The engine always keeps the best strategy found — bad experiments cost nothing.")
        gap_target = bench.get("Best possible (perfect foresight)")
        if gap_target is not None:
            st.caption(f"Perfect-foresight bound: {gap_target:,.2f} {unit} — no strategy "
                       "without a crystal ball can beat this.")
    if cur["code"]:
        with st.expander("Read the strategy the AI actually wrote this round (real code)"):
            st.code(cur["code"], language="python")
    st.info(
        "**Why this matters:** the strategies are ordinary, readable code — an engineer can audit "
        "every rule. And the whole search you just replayed cost a few dozen AI calls, not weeks "
        "of training. The honest finding is also on screen: the AI plateaus above the perfect-"
        "foresight line — which is why the engine keeps a classical optimizer in the room and "
        "lets the scoreboard pick the winner.")
