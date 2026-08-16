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

tab1, tab2, tab3, tab4 = st.tabs(["1 · The problem", "2 · The engine",
                                  "3 · Watch the AI improve itself", "🔍 Under the hood"])

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

    st.markdown("### 📋 The operational plan the engine hands the data center")
    firm_mw = dc.profile_mw * (1 - dc.deferrable_frac)
    arrive_mw = dc.profile_mw * dc.deferrable_frac
    flex_served = opt.served_mw - firm_mw - opt.battery_mw
    def compute_action(s, a):
        if a <= 0: return "▶ run normally"
        if s < 0.5 * a: return "⏸ PAUSE deferrable compute"
        if s > 1.25 * a: return "⏩ CATCH UP backlog"
        return "▶ run normally"
    def battery_action(x):
        if x > 5: return "🔋 CHARGE"
        if x < -5: return "⚡ DISCHARGE"
        return "— idle"
    plan = pd.DataFrame({
        "time": test["time"],
        "price $/MWh": np.round(impact.price_base, 1),
        "compute action": [compute_action(s, a) for s, a in zip(flex_served, arrive_mw)],
        "battery action": [battery_action(x) for x in opt.battery_mw],
        "grid draw MW": np.round(opt.served_mw, 0),
        "battery MW": np.round(opt.battery_mw, 0),
        "backlog MWh": np.round(opt.deferred_backlog, 0),
    })

    hh = test["time"].dt.hour.values
    pause_hours = sorted(h for h in range(24)
                         if np.median(flex_served[hh == h]) < 0.5 * np.median(arrive_mw[hh == h] + 1e-9))
    catchup_hours = sorted(h for h in range(24)
                           if np.median(flex_served[hh == h]) > 1.25 * np.median(arrive_mw[hh == h] + 1e-9))
    dis_hours = sorted(h for h in range(24) if np.median(opt.battery_mw[hh == h]) < -5)
    chg_hours = sorted(h for h in range(24) if np.median(opt.battery_mw[hh == h]) > 5)
    def hrs(hs):
        return ", ".join(f"{h:02d}:00" for h in hs) if hs else "—"
    st.markdown(f"""
**Standing orders (typical day):**
- **Pause deferrable compute** around: {hrs(pause_hours)} (the price-setting hours)
- **Catch up the backlog** around: {hrs(catchup_hours)} (cheap hours; nothing waits past its 24 h deadline)
- **Discharge the battery** around: {hrs(dis_hours)} · **recharge** around: {hrs(chg_hours)}
- Battery active {(np.abs(opt.battery_mw) > 5).mean() * 100:.0f}% of hours; max backlog reached {opt.deferred_backlog.max():.0f} MWh
- Bottom line: **${(naive.energy_cost - opt.energy_cost)/1e6:.2f}M saved on its own bill** and **${(naive.system_cost_delta - opt.system_cost_delta)/1e6:.2f}M less system cost** over the period, serving identical compute.
""")
    prof = pd.DataFrame({
        "hour": range(24),
        "avg grid draw MW": [opt.served_mw[hh == h].mean() for h in range(24)],
        "avg battery MW": [opt.battery_mw[hh == h].mean() for h in range(24)],
        "avg price $/MWh": [impact.price_base[hh == h].mean() for h in range(24)],
    }).set_index("hour")
    pc1, pc2 = st.columns([2, 3])
    with pc1:
        st.markdown("**The shape of a typical day** (draw follows cheap hours)")
        st.line_chart(prof, height=240)
    with pc2:
        worst_day = test["time"].dt.date.iloc[int(np.argmax(impact.price_new - impact.price_base))]
        st.markdown(f"**Hour-by-hour playbook for the hardest day ({worst_day})**")
        day_plan = plan[test["time"].dt.date.values == worst_day]
        st.dataframe(day_plan.assign(time=day_plan["time"].dt.strftime("%H:%M")),
                     height=240, hide_index=True)
    st.download_button("Download the full dispatch schedule (CSV)",
                       plan.to_csv(index=False).encode(), "dispatch_plan.csv", "text/csv")

# ---------------------------------------------------------------- tab 4
with tab4:
    st.subheader("Full transparency: prompts, data, and logs")
    sec = st.radio("What do you want to inspect?",
                   ["Prompt templates", "Initial data & assumptions", "Run logs & transcripts"],
                   horizontal=True)

    if sec == "Prompt templates":
        st.markdown("These are the **exact instructions** each AI receives — nothing else "
                    "is sent. `{task_description}` is filled in by the coach each round.")
        from engine import aps_dispatch as _P
        colA, colB = st.columns(2)
        with colA:
            st.markdown("**Strategy writer** (generation prompt)")
            st.code(_P.GENERATION_PROMPT, language="text")
            st.markdown("**Bug fixer** (repair prompt, used after a crash)")
            st.code(_P.REPAIR_PROMPT, language="text")
        with colB:
            st.markdown("**Coach** (meta prompt — sees scores, decides refine vs rethink)")
            st.code(_P.META_PROMPT, language="text")
            st.markdown("**Required code structure** (the contract every strategy must fit)")
            st.code(_P.POLICY_SIGNATURE, language="python")
        with st.expander("Battery-paper replication prompts (aps/prompts.py)"):
            from aps import prompts as _BP
            st.code(_BP.GENERATION_PROMPT, language="text")
            st.code(_BP.META_PROMPT, language="text")

    elif sec == "Initial data & assumptions":
        df, test, twin, iso = load_twin()
        st.markdown(f"**Market data**: {iso} day-ahead LMP, load, and fuel mix, "
                    f"{df['time'].min().date()} → {df['time'].max().date()} "
                    f"({len(df)} hourly rows; last {len(test)} held out for everything shown in the demo).")
        st.dataframe(df.head(200), height=240)
        st.download_button("Download the full dataset (CSV)",
                           df.to_csv(index=False).encode(), "nyiso_dataset.csv", "text/csv")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Twin calibration (held-out weeks)**")
            st.json({"MAE $/MWh": round(twin.report.mae, 2),
                     "RMSE $/MWh": round(twin.report.rmse, 2),
                     "correlation": round(twin.report.corr, 3),
                     "peak-decile MAE $/MWh": round(twin.report.peak_mae, 2),
                     "train hours": twin.report.n_train, "test hours": twin.report.n_test})
        with c2:
            st.markdown("**Scenario assumptions (data center)**")
            st.json({"size": "set by the Act-2 slider (default 500 MW)",
                     "firm load": "50% (cannot move)",
                     "deferrable compute window": "24 h deadline, enforced",
                     "battery round-trip efficiency": "88%",
                     "battery sizing": "slider MW × 4 h of storage"})
        for run_name, label in [("results/engine_aps", "APS-over-engine run"),
                                ("results/run1", "Battery replication run")]:
            p = ROOT / run_name
            if p.exists():
                with st.expander(f"{label}: initial configuration & benchmarks"):
                    for f in ["baselines.json", "config.json", "summary.json"]:
                        if (p / f).exists():
                            st.markdown(f"`{run_name}/{f}`")
                            st.json(json.loads((p / f).read_text()))

    else:  # Run logs & transcripts
        run_dirs = [d for d in sorted((ROOT / "results").iterdir())
                    if d.is_dir() and list(d.glob("episode_*"))]
        rd = st.selectbox("Run", [d.name for d in run_dirs])
        run_dir = ROOT / "results" / rd
        ev = run_dir / "events.jsonl"
        if ev.exists():
            st.markdown("**Event log** (every score, coach call, and crash)")
            st.dataframe(pd.DataFrame([json.loads(l) for l in ev.read_text().splitlines()]),
                         height=220)
        wl = run_dir / "worker.log"
        if wl.exists():
            with st.expander("Worker log (raw)"):
                st.code(wl.read_text()[-4000:], language="text")
        ep = st.selectbox("Episode", [d.name for d in sorted(run_dir.glob("episode_*"))])
        ep_dir = run_dir / ep
        st.markdown("**Episode state** (the machine-readable record of every round)")
        st.json(json.loads((ep_dir / "state.json").read_text()), expanded=False)
        tdir = ep_dir / "transcript"
        if tdir.exists() and list(tdir.glob("*")):
            st.markdown("**Verbatim transcript** — every prompt sent and every response received")
            exch = st.selectbox("Exchange", sorted({f.name.rsplit(".", 2)[0]
                                                    for f in tdir.glob("*.txt")}))
            pf, rf = tdir / f"{exch}.prompt.txt", tdir / f"{exch}.response.txt"
            cA, cB = st.columns(2)
            with cA:
                st.markdown("📤 **Prompt sent to the AI**")
                if pf.exists():
                    st.code(pf.read_text(), language="text")
            with cB:
                st.markdown("📥 **AI response, verbatim**")
                if rf.exists():
                    st.code(rf.read_text(), language="text")
        else:
            st.info("This run predates transcript archiving — prompts/responses were consumed "
                    "in place. Every run started from now on keeps the full verbatim transcript. "
                    "Available for old runs: per-round strategy code (Act 3), event log, and state.")

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
        has_scores = any(json.loads(f.read_text())["records"]
                         for f in d.glob("episode_*/state.json"))
        if (d / "baselines.json").exists() and has_scores:
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

    if not recs:
        st.info("This episode has no scored rounds yet — pick another, or check Live mode.")
        st.stop()

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
    # ---- the story of this round: instruction -> strategy -> score -> verdict
    st.markdown("#### 🧵 The story of this round")
    coach_by_iter = {}
    ev_file = run_dir / "events.jsonl"
    if ev_file.exists():
        for l in ev_file.read_text().splitlines():
            e = json.loads(l)
            if e["episode"] == ep_name and e["kind"].startswith("coach"):
                coach_by_iter[e["iteration"]] = (e["kind"].split(":")[1], e["detail"])
    tdir = run_dir / ep_name / "transcript"
    gen_prompts = sorted(tdir.glob("*gen_pending.prompt.txt")) if tdir.exists() else []

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        st.markdown("**1 · 📥 Instruction given**")
        it = cur["iteration"]
        if it in coach_by_iter:
            st.caption(f"from the coach ({coach_by_iter[it][0]} mode)")
            st.markdown(coach_by_iter[it][1][:450] + "…")
        elif it == 0:
            st.caption("the starting task, no feedback yet")
            st.markdown("_“Implement a rule-based dispatch policy that decides, from the "
                        "current price and local state, when to run deferrable compute… "
                        "minimizing the added system dispatch cost.”_")
        else:
            st.caption("coach text not archived for this old run — mode was:")
            st.markdown(f"**{cur['mode']}**")
        if it < len(gen_prompts):
            with st.expander("full prompt, verbatim"):
                st.code(gen_prompts[it].read_text(), language="text")
    with s2:
        st.markdown("**2 · 🧠 Strategy produced**")
        if cur["repairs"]:
            st.caption(f"crashed {cur['repairs']}×, self-repaired, then ran")
        if cur["code"]:
            st.code("\n".join(cur["code"].splitlines()[:16]) + "\n…", language="python")
    with s3:
        st.markdown("**3 · 🎯 Result**")
        st.metric("score", f"{cur['cost']/scale:,.2f} {unit}")
        prev_best = min((h["cost"] for h in hist[:-1]), default=None)
        if prev_best is not None:
            d = (cur["cost"] - prev_best) / scale
            st.markdown("**new best — kept ✅**" if d < 0 else f"worse than best by {abs(d):,.2f} — discarded 🗑")
    with s4:
        st.markdown("**4 · 🗣 Coach's verdict**")
        nxt = coach_by_iter.get(cur["iteration"] + 1)
        if nxt:
            st.caption(f"→ shaped round {cur['iteration'] + 2} ({nxt[0]} mode)")
            st.markdown(nxt[1][:450] + "…")
        elif shown < max_iter:
            st.caption("verdict text not archived; the decision was:")
            st.markdown(f"**{recs[shown]['mode']}** → round {cur['iteration'] + 2}")
        else:
            st.markdown("_final round — search ended, best strategy kept._")

    if cur["code"]:
        with st.expander("Read the full strategy the AI wrote this round (real code)"):
            st.code(cur["code"], language="python")
    st.info(
        "**Why this matters:** the strategies are ordinary, readable code — an engineer can audit "
        "every rule. And the whole search you just replayed cost a few dozen AI calls, not weeks "
        "of training. The honest finding is also on screen: the AI plateaus above the perfect-"
        "foresight line — which is why the engine keeps a classical optimizer in the room and "
        "lets the scoreboard pick the winner.")
