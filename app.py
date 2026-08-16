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


ZONES = {  # NYISO load-zone centroids
    "WEST": (42.90, -78.85), "GENESE": (43.00, -77.60), "CENTRL": (43.05, -76.15),
    "NORTH": (44.70, -73.50), "MHK VL": (43.10, -75.20), "CAPITL": (42.65, -73.75),
    "HUD VL": (41.70, -73.90), "MILLWD": (41.20, -73.80), "DUNWOD": (40.95, -73.85),
    "N.Y.C.": (40.75, -73.98), "LONGIL": (40.80, -73.10),
}


@st.cache_data(show_spinner="Loading zonal prices for the map…")
def load_zonal_day(day: str) -> pd.DataFrame:
    import gridstatus
    lmp = gridstatus.NYISO().get_lmp(date=day, market="DAY_AHEAD_HOURLY")
    lmp = lmp[lmp["Location"].isin(ZONES)]
    lmp["hour"] = lmp["Interval Start"].dt.hour
    return lmp[["hour", "Location", "LMP"]]


def price_map(day_df: pd.DataFrame, hour: int):
    import pydeck as pdk
    snap = day_df[day_df["hour"] == hour].copy()
    snap["lat"] = snap["Location"].map(lambda z: ZONES[z][0])
    snap["lon"] = snap["Location"].map(lambda z: ZONES[z][1])
    pmax = max(float(day_df["LMP"].max()), 1.0)
    snap["frac"] = (snap["LMP"] / pmax).clip(0, 1)
    snap["color"] = snap["frac"].map(
        lambda f: [int(40 + 215 * f), int(180 * (1 - f) + 40), 60, 200])
    snap["radius"] = 8000 + snap["frac"] * 32000
    snap["label"] = snap.apply(lambda r: f"${r.LMP:.0f}", axis=1)
    layers = [
        pdk.Layer("ScatterplotLayer", snap, get_position="[lon, lat]",
                  get_fill_color="color", get_radius="radius", pickable=True),
        pdk.Layer("TextLayer", snap, get_position="[lon, lat]", get_text="label",
                  get_size=14, get_color=[255, 255, 255, 255]),
    ]
    return pdk.Deck(layers=layers,
                    initial_view_state=pdk.ViewState(latitude=42.7, longitude=-75.5, zoom=5.6),
                    tooltip={"text": "{Location}: {label}/MWh"},
                    map_style=None)


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

    st.divider()
    st.markdown("### The grid on a map — one price per region, changing every hour")
    spike_day = str(df.loc[df["LMP"].idxmax(), "time"].date())
    day = st.date_input("Day", value=pd.to_datetime(spike_day).date(),
                        min_value=df["time"].min().date(), max_value=df["time"].max().date())
    hour = st.slider("Hour of day", 0, 23, 18,
                     help="Drag through the day — watch prices climb into the evening")
    try:
        day_df = load_zonal_day(str(day))
        st.pydeck_chart(price_map(day_df, hour), height=420)
        st.caption("Real NYISO zonal day-ahead prices. Bigger, redder = more expensive. "
                   f"The preselected day is the most expensive hour in the dataset ({spike_day}).")
    except Exception as e:
        st.warning(f"Map data unavailable ({e}); the price chart above tells the same story.")

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

    live = st.toggle("🔴 Live mode — run the AI right now and watch it deliberate")
    if live:
        import signal
        import subprocess
        import time as _time

        live_runs = sorted((ROOT / "results").glob("live_*")) + \
                    ([ROOT / "results/engine_live"] if (ROOT / "results/engine_live").exists() else [])
        default_dir = st.session_state.get("live_dir") or (str(live_runs[-1]) if live_runs else None)

        cc1, cc2, cc3, cc4 = st.columns([1, 1, 1, 2])
        n_ep = cc1.number_input("Episodes", 1, 5, 2)
        n_it = cc2.number_input("Rounds each", 3, 10, 6)
        if cc3.button("🚀 Start new run", type="primary",
                      help="Sets up the twin and launches the AI worker in the background. "
                           "Needs an authenticated `claude` CLI or ANTHROPIC_API_KEY in the "
                           "environment you launched streamlit from."):
            run_name = f"results/live_{_time.strftime('%m%d_%H%M%S')}"
            with st.spinner("Calibrating the twin and computing the benchmarks…"):
                subprocess.run(
                    [sys.executable, "experiments/driver2.py", "init", "--run", run_name,
                     "--episodes", str(int(n_ep)), "--iterations", str(int(n_it))],
                    cwd=ROOT, check=True, capture_output=True)
            logf = open(ROOT / run_name / "worker.log", "w")
            proc = subprocess.Popen(
                [sys.executable, "experiments/worker.py", "--run", run_name],
                cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
            (ROOT / run_name / "worker.pid").write_text(str(proc.pid))
            st.session_state["live_dir"] = run_name
            st.rerun()
        if default_dir and (Path(default_dir) if Path(default_dir).is_absolute()
                            else ROOT / default_dir).joinpath("worker.pid").exists():
            if cc4.button("⏹ Stop worker"):
                pid_file = (Path(default_dir) if Path(default_dir).is_absolute()
                            else ROOT / default_dir) / "worker.pid"
                try:
                    import os as _os
                    _os.kill(int(pid_file.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                pid_file.unlink(missing_ok=True)
                st.rerun()

        @st.fragment(run_every="4s")
        def live_feed():
            if not default_dir:
                st.info("No live run yet — press **Start new run** above (or launch "
                        "`experiments/worker.py` from a terminal).")
                return
            live_dir = Path(default_dir) if Path(default_dir).is_absolute() else ROOT / default_dir
            st.caption(f"Watching `{live_dir.name}`")
            ev_file = live_dir / "events.jsonl"
            if not ev_file.exists():
                st.info("Run starting — the first strategies are being written and scored…")
                log = live_dir / "worker.log"
                if log.exists():
                    st.code(log.read_text()[-800:] or "…", language="text")
                return
            events = [json.loads(l) for l in ev_file.read_text().splitlines()]
            bl = json.loads((live_dir / "baselines.json").read_text())
            if not (live_dir / "worker.pid").exists():
                st.success("Run finished — flip Live mode off to replay it round by round "
                           "(it now appears in the recorded-runs list).")
            scores = [e for e in events if e["kind"] == "score"]
            c1, c2, c3 = st.columns(3)
            c1.metric("Rounds scored so far", len(scores))
            c2.metric("Coach interventions",
                      sum(1 for e in events if e["kind"].startswith("coach")))
            c3.metric("Crashes self-repaired",
                      sum(1 for e in events if e["kind"] == "crash"))
            if scores:
                costs = [float(e["detail"].split("$")[1].split(" ")[0].replace(",", ""))
                         for e in scores]
                live_df = pd.DataFrame({
                    "round": range(1, len(costs) + 1),
                    "attempt ($M)": [c / 1e6 for c in costs]}).set_index("round")
                live_df["Do nothing"] = bl["naive"] / 1e6
                live_df["Best possible"] = bl["dla"] / 1e6
                st.line_chart(live_df, height=240)
            st.markdown("**Deliberation feed** (newest first):")
            icons = {"score": "🎯", "crash": "💥"}
            for e in reversed(events[-12:]):
                icon = icons.get(e["kind"], "🧠")
                who = "Coach" if e["kind"].startswith("coach") else \
                      ("Score" if e["kind"] == "score" else "Crash")
                st.markdown(f"{icon} **{e['episode']} · round {e['iteration'] + 1} · {who}** — "
                            f"{e['detail'][:220]}{'…' if len(e['detail']) > 220 else ''}")
        live_feed()
        st.stop()

    runs = {}
    for d in sorted((ROOT / "results").glob("live_*")):
        if (d / "baselines.json").exists() and list(d.glob("episode_*/state.json")):
            bl = json.loads((d / "baselines.json").read_text())
            runs[f"Live run {d.name.replace('live_', '')} — $M added system cost"] = \
                (d, "$M", {"Do nothing": bl["naive"]/1e6, "Hand-written rules": bl["pfa"]/1e6,
                           "Best possible (perfect foresight)": bl["dla"]/1e6})
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
