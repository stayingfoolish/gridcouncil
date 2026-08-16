"""Grid Optimization Engine — story-first demo for a non-technical audience.

    streamlit run app.py

Information architecture (story-first, live separated):
  1. The problem            — shared opening: the marginal-price auction, real data
  2. Story: Home battery    — precomputed narrative (simulated week, EUR)
  3. Story: Data center     — precomputed narrative + interactive scenario (real NYISO, USD)
  4. How the agents talk    — the coach / strategy-writer / twin message loop
  5. Live Lab: Home         — kick off APS on the home battery, watch it deliberate
  6. Live Lab: Data center  — kick off APS on the dispatch problem, watch it deliberate
  7. Under the hood         — prompts, data, logs, verbatim transcripts
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.data import YEAR_END, YEAR_START, fetch
from engine.optimizer import dispatch_dla, dispatch_pfa, evaluate
from engine.scenario import assess, datacenter
from engine.twin import MeritOrderTwin

st.set_page_config(page_title="Grid Optimization Engine", page_icon="⚡",
                   layout="wide")

EUR_BENCH = {"No battery": 10.64, "Best possible (perfect foresight)": -6.32}


@st.cache_resource(show_spinner="Loading a year of market data and calibrating the twin…")
def load_twin():
    df, iso = fetch(YEAR_START, YEAR_END)
    df = df.sort_values("time").reset_index(drop=True)
    # a year of context; the operational twin is calibrated on the most recent
    # 90 days (supply stacks drift with season and fuel prices)
    twin = MeritOrderTwin.calibrate(df.iloc[-2160:].reset_index(drop=True))
    # scenario window: the last 15 days (inside the twin's held-out tail)
    test = df.iloc[-360:].reset_index(drop=True)
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


def load_aps_run(run_dir: Path):
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


# ================================================================ reusable UI

def replay_ui(runs: dict, keyp: str):
    """Round-by-round replay of a recorded APS run, with the story of each round."""
    if not runs:
        st.info("No recorded runs available yet.")
        return
    run_name = st.selectbox("Recorded run", list(runs), key=f"{keyp}_run")
    run_dir, unit, bench = runs[run_name]
    episodes = load_aps_run(run_dir)
    ep_name = st.selectbox("Episode (independent attempt)", list(episodes), key=f"{keyp}_ep")
    recs = [r for r in episodes[ep_name] if r["cost"] is not None]
    scale = 1e6 if unit == "$M" else 1.0
    if not recs:
        st.info("This episode has no scored rounds yet.")
        return

    max_iter = len(recs)
    shown = st.slider("Play through the rounds ▶", 1, max_iter, 1, key=f"{keyp}_round",
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
        st.line_chart(plot_df, height=300)
    with rc:
        mode = cur["mode"] if shown > 1 else "initial"
        st.markdown(f"### Round {cur['iteration'] + 1}")
        st.markdown(NARRATION.get(mode, ""))
        if cur["repairs"]:
            st.warning(f"🔧 Crashed {cur['repairs']} time(s); the AI read the error and "
                       "repaired its own code before this score.")
        st.metric("This attempt's score", f"{cur['cost']/scale:,.2f} {unit}")
        st.metric("Best so far", f"{best_so_far/scale:,.2f} {unit}",
                  help="The engine always keeps the best strategy found — bad experiments cost nothing.")
        gap_target = bench.get("Best possible (perfect foresight)")
        if gap_target is not None:
            st.caption(f"Perfect-foresight bound: {gap_target:,.2f} {unit} — no strategy "
                       "without a crystal ball can beat this.")

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
            st.markdown("_“Implement a rule-based policy that decides, from the current "
                        "state, when to act… minimizing total cost.”_")
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
            st.markdown("**new best — kept ✅**" if d < 0
                        else f"worse than best by {abs(d):,.2f} — discarded 🗑")
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

    bt = run_dir / ep_name / "best_trace.json"
    if bt.exists():
        trace = json.loads(bt.read_text())
        reasons = trace.get("reasons") or []
        if any(reasons):
            with st.expander("🧾 Decision ledger — the best strategy explains every hour, "
                             "in its own words"):
                acts = trace.get("actions_kw") or trace.get("served_mw") or []
                ledger = pd.DataFrame({
                    "hour": range(len(reasons)),
                    "action": [round(a, 2) for a in acts[:len(reasons)]],
                    "the strategy's own reason": reasons,
                })
                st.dataframe(ledger[ledger["the strategy's own reason"] != ""].head(96),
                             height=260, hide_index=True)
        else:
            st.caption("🧾 Decision ledger: this run predates self-explaining strategies — "
                       "new runs record the strategy's own one-line reason for every hour.")


def live_lab(flavor: dict, keyp: str):
    """Start + watch a live APS run. flavor: name, driver, prefix, unit, scale,
    naive_label, best_label, default_ep, default_it."""
    import signal
    import subprocess
    import time as _time

    st.markdown(f"Start a **real self-improvement run on the {flavor['name']}** and watch "
                "it deliberate. Requires an authenticated `claude` CLI or "
                "`ANTHROPIC_API_KEY` in the environment you launched streamlit from.")
    live_runs = sorted((ROOT / "results").glob(f"{flavor['prefix']}*"))
    default_dir = st.session_state.get(f"{keyp}_dir") or (str(live_runs[-1]) if live_runs else None)

    cc1, cc2, cc3, cc4 = st.columns([1, 1, 1, 2])
    n_ep = cc1.number_input("Episodes", 1, 5, flavor["default_ep"], key=f"{keyp}_ne")
    n_it = cc2.number_input("Rounds each", 3, 10, flavor["default_it"], key=f"{keyp}_ni")
    if cc3.button("🚀 Start new run", type="primary", key=f"{keyp}_start"):
        run_name = f"results/{flavor['prefix']}{_time.strftime('%m%d_%H%M%S')}"
        with st.spinner("Setting up the environment and computing the benchmarks…"):
            subprocess.run(
                [sys.executable, flavor["driver"], "init", "--run", run_name,
                 "--episodes", str(int(n_ep)), "--iterations", str(int(n_it))],
                cwd=ROOT, check=True, capture_output=True)
        logf = open(ROOT / run_name / "worker.log", "w")
        proc = subprocess.Popen(
            [sys.executable, "experiments/worker.py", "--run", run_name,
             "--driver", flavor["driver"]],
            cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        (ROOT / run_name / "worker.pid").write_text(str(proc.pid))
        st.session_state[f"{keyp}_dir"] = run_name
        st.rerun()
    if default_dir and (ROOT / default_dir).joinpath("worker.pid").exists():
        sp1, sp2 = cc4.columns(2)
        if sp1.button("⚡ Inject spike", key=f"{keyp}_spike", type="secondary",
                      help="Perturb the world mid-run: demand spike (data center) or "
                           "price surge (home). The next rounds are scored against the "
                           "harder world — watch the coach react."):
            subprocess.run([sys.executable, "experiments/inject.py",
                            "--run", str(default_dir)], cwd=ROOT, capture_output=True)
            st.toast("Spike injected — the world just got harder.", icon="⚡")
        if sp2.button("⏹ Stop worker", key=f"{keyp}_stop"):
            pid_file = ROOT / default_dir / "worker.pid"
            try:
                import os as _os
                _os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
            pid_file.unlink(missing_ok=True)
            st.rerun()

    @st.fragment(run_every="4s")
    def live_feed():
        if not default_dir:
            st.info("No live run yet — press **Start new run** above (or launch "
                    "`experiments/worker.py` from a terminal).")
            return
        live_dir = ROOT / default_dir
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
            st.success("Run finished — it now appears in this story's recorded-runs list "
                       "for round-by-round replay.")
        scores = [e for e in events if e["kind"] == "score"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Rounds scored so far", len(scores))
        c2.metric("Coach interventions",
                  sum(1 for e in events if e["kind"].startswith("coach")))
        c3.metric("Crashes self-repaired",
                  sum(1 for e in events if e["kind"] == "crash"))
        vals = [e.get("value") for e in scores if e.get("value") is not None]
        if vals:
            sc = flavor["scale"]
            live_df = pd.DataFrame({
                "round": range(1, len(vals) + 1),
                f"attempt ({flavor['unit']})": [v / sc for v in vals]}).set_index("round")
            live_df[flavor["naive_label"]] = bl["naive"] / sc
            live_df[flavor["best_label"]] = bl["dla"] / sc
            st.line_chart(live_df, height=240)
        st.markdown("**Deliberation feed** (newest first):")
        icons = {"score": "🎯", "crash": "💥", "spike": "⚡"}
        for e in reversed(events[-12:]):
            icon = icons.get(e["kind"], "🧠")
            who = ("Coach" if e["kind"].startswith("coach") else
                   "Score" if e["kind"] == "score" else
                   "WORLD CHANGED" if e["kind"] == "spike" else "Crash")
            st.markdown(f"{icon} **{e['episode']} · round {e['iteration'] + 1} · {who}** — "
                        f"{e['detail'][:220]}{'…' if len(e['detail']) > 220 else ''}")
    live_feed()


def dollar_runs():
    """Recorded $-denominated runs (data-center dispatch + finished live runs)."""
    runs = {}
    if (ROOT / "results/engine_aps").exists():
        bl = json.loads((ROOT / "results/engine_aps/baselines.json").read_text())
        runs["Data-center dispatch (the original recorded search)"] = \
            (ROOT / "results/engine_aps", "$M",
             {"Do nothing": bl["naive"]/1e6, "Hand-written rules": bl["pfa"]/1e6,
              "Best possible (perfect foresight)": bl["dla"]/1e6})
    for d in sorted((ROOT / "results").glob("live_*")):
        has_scores = any(json.loads(f.read_text())["records"]
                         for f in d.glob("episode_*/state.json"))
        if (d / "baselines.json").exists() and has_scores:
            bl = json.loads((d / "baselines.json").read_text())
            runs[f"Live run {d.name.replace('live_', '')}"] = \
                (d, "$M", {"Do nothing": bl["naive"]/1e6,
                           "Best possible (perfect foresight)": bl["dla"]/1e6})
    return runs


def euro_runs():
    runs = {}
    if (ROOT / "results/run1").exists():
        runs["Home battery (the original recorded search, 10 attempts)"] = \
            (ROOT / "results/run1", "€", EUR_BENCH)
    for d in sorted((ROOT / "results").glob("bliv_*")):
        has_scores = any(json.loads(f.read_text())["records"]
                         for f in d.glob("episode_*/state.json"))
        if (d / "baselines.json").exists() and has_scores:
            bl = json.loads((d / "baselines.json").read_text())
            runs[f"Live run {d.name.replace('bliv_', '')}"] = \
                (d, "€", {"No battery": bl["naive"],
                          "Best possible (perfect foresight)": bl["dla"]})
    return runs


AGENT_BOX = """
<div style="display:flex;gap:10px;align-items:stretch;flex-wrap:wrap;font-size:0.92rem">
 <div style="flex:1;min-width:180px;border:2px solid #7c4dbe;border-radius:10px;padding:10px">
   <b>🧑‍🏫 The Coach</b> <i>(meta-level LLM)</i><br>
   Sees: scores, utilization, history — <b>never raw data</b>.<br>
   Says: “refine this” or “rethink entirely”.
 </div>
 <div style="align-self:center;font-size:1.4rem">→<br><span style="font-size:.7rem">instruction</span></div>
 <div style="flex:1;min-width:180px;border:2px solid #2c7fb8;border-radius:10px;padding:10px">
   <b>👩‍💻 The Strategy Writer</b> <i>(code-writing LLM)</i><br>
   Sees: the task, the rules of the system, the coach's note.<br>
   Produces: a complete strategy <b>as ordinary code</b>.
 </div>
 <div style="align-self:center;font-size:1.4rem">→<br><span style="font-size:.7rem">code</span></div>
 <div style="flex:1;min-width:180px;border:2px solid #33a02c;border-radius:10px;padding:10px">
   <b>⚖️ The Grid Twin</b> <i>(simulator — not an AI)</i><br>
   Runs the code against real market data.<br>
   Returns: a <b>score</b> — or a crash report.
 </div>
 <div style="align-self:center;font-size:1.4rem">↩<br><span style="font-size:.7rem">score / error</span></div>
</div>
"""

# ================================================================ layout

st.title("⚡ Grid Optimization Engine")
st.caption("A live model of a real power grid, an optimizer that keeps new demand from "
           "raising everyone's bill, and AI agents that teach themselves control "
           "strategies. All numbers come from real market data or fully disclosed simulations.")

(t_tour, t_mission, t_prob, t_home, t_dc, t_coord, t_today, t_who, t_agents,
 t_live_home, t_live_dc, t_hood) = st.tabs([
    "🎯 Tour", "🛰 Mission Control",
    "1 · The problem", "2 · 🏠 Story: Home battery", "3 · 🏢 Story: Data center",
    "4 · 🌊 2035: Why coordination", "4b · 🧘 Today: The calm story",
    "4c · ⚖️ Who pays?", "5 · 🤝 How the agents talk",
    "🔴 Live Lab: Home", "🔴 Live Lab: Data center", "🔍 Under the hood"])

# ---------------------------------------------------------------- the problem
with t_prob:
    df, test, twin, iso = load_twin()
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Electricity is an auction — the most expensive power plant sets everyone's price")
        st.markdown(
            f"""Every hour, the grid ({iso}, real data, {len(df)//24} days) buys exactly as much
power as people use. Cheap plants run first; when demand climbs, more expensive
plants switch on — and **that last, most expensive plant sets the price everyone pays**.

That's why one badly-timed gigawatt — a data center, an EV rush hour — can raise
the bill of every home on the grid. And the same logic scales down: a single home
with solar panels faces the same question every hour — **use, store, or sell?**

Two protagonists, same physics: tab 2 follows a **home with a battery**;
tab 3 follows a **500 MW data center**. In both, AI agents learn the strategy —
and a calibrated twin of the market keeps the score.""")
        m1, m2, m3 = st.columns(3)
        m1.metric("Typical price", f"${df['LMP'].median():.0f}/MWh")
        m2.metric("Worst hour in the data", f"${df['LMP'].max():.0f}/MWh",
                  f"{df['LMP'].max()/df['LMP'].median():.0f}× the typical", delta_color="inverse")
        m3.metric("Twin accuracy (unseen weeks)", f"{twin.report.corr:.0%} correlation")
        m4, m5 = st.columns(2)
        m4.metric("Grid carbon intensity (avg)",
                  f"{df['carbon_t_per_mwh'].mean()*1000:.0f} kg CO₂/MWh",
                  help="Computed hourly from the real fuel mix")
        m5.metric("Dirtiest vs cleanest hour",
                  f"{df['carbon_t_per_mwh'].max()/max(df['carbon_t_per_mwh'].min(),1e-9):.1f}×",
                  help="Shifting load between hours changes real emissions, not just cost")
    with right:
        st.markdown("**Real prices, hour by hour** — spikes are expensive plants switching on")
        chart_df = pd.DataFrame({"time": df["time"], "$/MWh": df["LMP"]}).set_index("time")
        st.line_chart(chart_df, height=190)
        st.markdown("**…and every hour has a carbon intensity too** (real fuel mix)")
        st.line_chart(pd.DataFrame({"time": df["time"],
                                    "kg CO₂/MWh": df["carbon_t_per_mwh"]*1000}
                                   ).set_index("time"), height=140, color="#7a7a52")
    st.divider()
    st.markdown("### The grid on a map — one price per region, changing every hour")
    spike_day = str(df.loc[df["LMP"].idxmax(), "time"].date())
    day = st.date_input("Day", value=pd.to_datetime(spike_day).date(),
                        min_value=df["time"].min().date(), max_value=df["time"].max().date())
    hour = st.slider("Hour of day", 0, 23, 18)
    try:
        day_df = load_zonal_day(str(day))
        st.pydeck_chart(price_map(day_df, hour), height=420)
        st.caption("Real NYISO zonal day-ahead prices. Bigger, redder = more expensive. "
                   f"The preselected day is the most expensive in the dataset ({spike_day}).")
    except Exception as e:
        st.warning(f"Map data unavailable ({e}); the price chart above tells the same story.")

# ---------------------------------------------------------------- home story
with t_home:
    st.subheader("🏠 A home with solar and a battery — can an AI learn to run it?")
    st.markdown("""
**The problem.** A family has rooftop solar (5 kW), a 10 kWh battery, and a dynamic
electricity tariff (~0.35 €/kWh to buy, a fixed 0.08 € to sell back). Every hour someone —
or something — must decide: *charge the battery, discharge it, or trade with the grid?*
Get it wrong and solar power is sold cheap at noon and expensive power is bought at dinner.
The system is simulated over one week with fully disclosed assumptions.""")

    b1, b2, b3 = st.columns(3)
    b1.metric("Doing nothing (no battery)", "10.64 €", help="Cost of the week without any battery")
    b2.metric("Perfect crystal ball", "−6.32 €", help="A mathematical optimum with perfect foresight — the week turns a profit")
    b3.metric("Gap the AI must close", "16.96 €")

    st.markdown("""
**How we address it.** We gave the problem to the three-agent loop (tab 4): a strategy-writer
AI writes battery-control code, the simulator scores a full week, a coach AI reads the score
and steers the next attempt. **Ten independent searches**, ten rounds each — one hundred
strategies written, tested, and judged. No training, no examples of good behavior: only the
score as feedback.

**What happened.**""")
    r1, r2, r3 = st.columns(3)
    r1.metric("Searches that found a profitable strategy", "10 / 10")
    r2.metric("Best strategy found", "−6.08 €", "0.24 € from the theoretical optimum")
    r3.metric("Rounds needed to get there", "1–3", "not 1,000+ training episodes")

    ep1_trace = ROOT / "results/run1/episode_01/best_trace.json"
    if ep1_trace.exists():
        trace = json.loads(ep1_trace.read_text())
        soc = np.array(trace["soc_kwh"])[:-1]
        st.markdown("**A week in the life of the winning strategy** — it charges from midday "
                    "solar, runs the home through the expensive evening, every single day:")
        st.area_chart(pd.DataFrame({"hour of the week": range(len(soc)),
                                    "battery charge (kWh)": soc}).set_index("hour of the week"),
                      height=200)
    st.info("**The honest finding:** the AI found near-optimal strategies almost immediately — "
            "and further rounds often made things *worse*, which is why the loop always keeps "
            "the best strategy found rather than the latest. You can watch that happen below.")

    st.divider()
    st.markdown("### 🎬 Replay the recorded search, round by round")
    replay_ui(euro_runs(), "home")

# ---------------------------------------------------------------- dc story
with t_dc:
    st.subheader("🏢 A 500 MW data center wants to connect — what happens to everyone's bill?")
    st.markdown("""
**The problem.** AI data centers are the fastest-growing load on the grid. A 500 MW campus
draws as much power as ~400,000 homes — and if it runs flat-out through the evening peak,
it drags the market price up **for every consumer on the grid**. Utilities must answer:
*how much can we welcome, and on what terms?*

**How we address it.** The engine's calibrated twin re-runs the market **with** the new
load — first rigid, then with its own flexibility (deferrable compute, on-site storage)
dispatched intelligently. Try it yourself:""")

    c1, c2, c3, c4 = st.columns(4)
    mw = c1.slider("Data center size (MW)", 100, 1000, 500, 50)
    flex_pct = c2.slider("Compute that can wait a day (%)", 0, 80, 50, 10)
    batt_mw = c3.slider("On-site battery (MW)", 0, 300, 100, 50)
    use_lookahead = c4.toggle("Smart planner (looks ahead)", value=True)

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
    ci = test["carbon_t_per_mwh"].values
    tons_naive = float((dc.profile_mw * ci).sum())
    tons_opt = float((opt.served_mw * ci).sum())
    cb1, cb2 = st.columns(2)
    cb1.metric("Emissions, rigid", f"{tons_naive:,.0f} t CO₂",
               help="Grid-average carbon intensity per hour × the data center's draw")
    cb2.metric("Emissions, with the engine", f"{tons_opt:,.0f} t CO₂",
               f"{tons_opt - tons_naive:+,.0f} t", delta_color="inverse")
    p0, p1 = impact.price_base, impact.price_new
    worst = int(np.argmax(p1 - p0)); a, b = max(0, worst - 60), min(len(test), worst + 60)
    price_df = pd.DataFrame({
        "time": test["time"].iloc[a:b],
        "Before the data center": p0[a:b],
        "Rigid data center": p1[a:b],
        "With the engine": mitigated.price_new[a:b],
    }).set_index("time")
    st.line_chart(price_df, height=280)

    with st.expander("📋 The operational plan the engine hands the data center", expanded=False):
        firm_mw = dc.profile_mw * (1 - dc.deferrable_frac)
        arrive_mw = dc.profile_mw * dc.deferrable_frac
        flex_served = opt.served_mw - firm_mw - opt.battery_mw
        def compute_action(s, arr):
            if arr <= 0: return "▶ run normally"
            if s < 0.5 * arr: return "⏸ PAUSE deferrable compute"
            if s > 1.25 * arr: return "⏩ CATCH UP backlog"
            return "▶ run normally"
        def battery_action(x):
            if x > 5: return "🔋 CHARGE"
            if x < -5: return "⚡ DISCHARGE"
            return "— idle"
        plan = pd.DataFrame({
            "time": test["time"],
            "price $/MWh": np.round(impact.price_base, 1),
            "compute action": [compute_action(s, arr) for s, arr in zip(flex_served, arrive_mw)],
            "battery action": [battery_action(x) for x in opt.battery_mw],
            "grid draw MW": np.round(opt.served_mw, 0),
            "battery MW": np.round(opt.battery_mw, 0),
            "backlog MWh": np.round(opt.deferred_backlog, 0),
        })
        hh = test["time"].dt.hour.values
        dis_hours = sorted(h for h in range(24) if np.median(opt.battery_mw[hh == h]) < -5)
        chg_hours = sorted(h for h in range(24) if np.median(opt.battery_mw[hh == h]) > 5)
        catchup_hours = sorted(h for h in range(24)
                               if np.median(flex_served[hh == h]) > 1.25 * np.median(arrive_mw[hh == h] + 1e-9))
        def hrs(hs):
            return ", ".join(f"{h:02d}:00" for h in hs) if hs else "—"
        st.markdown(f"""
**Standing orders (typical day):** defer flexible compute through the expensive hours;
**catch up the backlog** around {hrs(catchup_hours)}; **discharge the battery** around
{hrs(dis_hours)}, **recharge** around {hrs(chg_hours)}. Nothing waits past its 24 h deadline.
Bottom line: **${(naive.energy_cost - opt.energy_cost)/1e6:.2f}M saved on its own bill**,
**${(naive.system_cost_delta - opt.system_cost_delta)/1e6:.2f}M less system cost**, identical compute served.""")
        prof = pd.DataFrame({
            "hour": range(24),
            "avg grid draw MW": [opt.served_mw[hh == h].mean() for h in range(24)],
            "avg battery MW": [opt.battery_mw[hh == h].mean() for h in range(24)],
            "avg price $/MWh": [impact.price_base[hh == h].mean() for h in range(24)],
        }).set_index("hour")
        pc1, pc2 = st.columns([2, 3])
        with pc1:
            st.markdown("**The shape of a typical day**")
            st.line_chart(prof, height=220)
        with pc2:
            worst_day = test["time"].dt.date.iloc[int(np.argmax(impact.price_new - impact.price_base))]
            st.markdown(f"**Hour-by-hour playbook, hardest day ({worst_day})**")
            day_plan = plan[test["time"].dt.date.values == worst_day]
            st.dataframe(day_plan.assign(time=day_plan["time"].dt.strftime("%H:%M")),
                         height=220, hide_index=True)
        st.download_button("Download the full dispatch schedule (CSV)",
                           plan.to_csv(index=False).encode(), "dispatch_plan.csv", "text/csv")

    st.markdown("### And can the AI agents learn this job too?")
    st.markdown("""We ran the same three-agent search on this problem — with a twist: here we
**know the mathematically best answer** (a perfect-foresight optimizer), so we can grade the AI
precisely. The ladder, from worst to best:""")
    l1, l2, l3, l4 = st.columns(4)
    l1.metric("Do nothing", "$14.9M", help="Added system cost, rigid data center")
    l2.metric("AI-searched strategy", "$14.1M", "closed 27% of the gap")
    l3.metric("Hand-written rules", "$13.3M", "closed 55%")
    l4.metric("Perfect foresight", "$12.0M", "the bound — 100%")
    st.info("**The honest finding:** on this harder problem the AI's reactive rules plateau "
            "well above the optimizer — timing a 24-hour backlog against price spikes needs "
            "foresight a simple rule can't express. That's exactly why the engine keeps a "
            "classical optimizer in the room and lets the scoreboard pick the winner.")

    st.divider()
    st.markdown("### 🎬 Replay the recorded search, round by round")
    replay_ui(dollar_runs(), "dc")


# ---------------------------------------------------------------- coordination story
@st.cache_data(show_spinner="Solving the three regimes (selfish, negotiated, coordinated)…")
def run_coordination(n_homes: int, dc_mw: int, extra_spike_mw: int, n_rounds: int,
                     n_evs: int = 0):
    import numpy as _np
    from engine.coordination import dc_actor, ev_actor, fleet_actor, run_scenario as _run
    df, test, twin, _ = load_twin()
    tail = df.iloc[-twin.report.n_test:].reset_index(drop=True)  # twin's held-out tail
    peak = int(tail["net_load_mw"].idxmax())
    a, b = max(0, peak - 36), min(len(tail), peak + 36)
    win = tail.iloc[a:b].reset_index(drop=True)
    T = len(win)
    base = win["net_load_mw"].values.copy()
    if extra_spike_mw:
        c = int(_np.argmax(base))
        w = _np.zeros(T); lo, hi = max(0, c - 12), min(T, c + 12)
        w[lo:hi] = extra_spike_mw * _np.hanning(hi - lo)
        base = base + w
    hour_adj = _np.array([twin.hour_adj.get(h, 0.0) for h in win["time"].dt.hour])
    actors = [dc_actor(T, mw=float(dc_mw)), fleet_actor(T, n_homes)]
    if n_evs:
        actors.append(ev_actor(T, n_evs, win["time"].dt.hour.values))
    res = _run(actors, base, twin.grid, twin.grid_price, hour_adj, n_rounds=n_rounds)
    price = res["price_fn"]
    out = {
        "times": win["time"], "base": base, "existing_mw": win["load_mw"].values,
        "p0": res["p0"],
        "selfish_net": res["selfish"]["net"],
        "joint_net": res["joint"]["net"],
        "rounds": [{"round": r["round"], "peak_mw": r["peak_mw"],
                    "peak_price": r["peak_price"]} for r in res["rounds"]],
        "p_selfish": price(res["selfish"]["net"]),
        "p_joint": price(res["joint"]["net"]),
        "fleet_batt": res["joint"]["dispatches"][1].battery_mw,
        "dc_draw": res["joint"]["dispatches"][0].draw_mw,
        "fleet_batt_selfish": res["selfish"]["dispatches"][1].battery_mw,
    }
    return out


with t_coord:
    st.subheader("🌊 Fast-forward the grid — when everyone's flexibility collides")
    st.markdown("""
**Today, flexible actors are too small to hurt each other** (see the 🧘 Today tab). But
electrification is compounding: home batteries, EV fleets, data-center clusters. This tab
**fast-forwards the fleet sizes on today's real grid** and shows the moment selfish
optimization starts manufacturing new peaks — and how coordination fixes it.""")

    ERAS = {
        "2026 — today": dict(homes=50_000, dc=500, evs=200_000),
        "2030 — the ramp": dict(homes=300_000, dc=1_500, evs=1_500_000),
        "2035 — full electrification": dict(homes=1_000_000, dc=3_000, evs=4_000_000),
    }
    e1, e2, e3 = st.columns([2, 1, 1])
    era = e1.radio("Choose an era (fleet sizes scale; the grid and prices stay 2026-real)",
                   list(ERAS), index=2, horizontal=True)
    extra_spike = e2.slider("Extra heat wave (MW)", 0, 2000, 0, 500)
    n_rounds = e3.slider("Negotiation rounds", 2, 8, 5)
    cfg = ERAS[era]
    st.caption(f"{cfg['homes']:,} battery homes · {cfg['dc']:,} MW of data centers · "
               f"{cfg['evs']:,} EVs charging overnight — on the real worst 3 days of our year.")

    C = run_coordination(cfg["homes"], cfg["dc"], extra_spike, n_rounds, cfg["evs"])
    times = pd.to_datetime(C["times"].values) if hasattr(C["times"], "values") else C["times"]

    st.markdown("#### Act 1 — What each future *adds to* the grid, hour by hour")
    st.markdown("Bars above zero are extra draw; below zero is relief. Watch selfish "
                "flexibility (red) pile everything into the same cheap hours:")
    delta_df = pd.DataFrame({
        "Selfish (everyone alone)": C["selfish_net"] - C["base"],
        "Coordinated": C["joint_net"] - C["base"],
    }, index=times)
    st.bar_chart(delta_df, height=280, color=["#e45756", "#54a24b"])

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Base peak", f"{C['base'].max():,.0f} MW")
    h2.metric("Selfish peak", f"{C['selfish_net'].max():,.0f} MW",
              f"{C['selfish_net'].max() - C['base'].max():+,.0f} MW", delta_color="inverse")
    h3.metric("Coordinated peak", f"{C['joint_net'].max():,.0f} MW",
              f"{C['joint_net'].max() - C['base'].max():+,.0f} MW")
    h4.metric("Peak price, selfish vs coord.",
              f"${C['p_joint'].max():,.0f}",
              f"{C['p_joint'].max() - C['p_selfish'].max():+,.0f} $/MWh")

    st.markdown("#### Act 2 — The peak hours under a microscope")
    pk = int(np.argmax(C["selfish_net"]))
    lo, hi = max(0, pk - 6), min(len(C["base"]), pk + 6)
    zoom = pd.DataFrame({
        "Grid alone": C["base"][lo:hi],
        "Selfish": C["selfish_net"][lo:hi],
        "Coordinated": C["joint_net"][lo:hi],
    }, index=[t.strftime("%a %H:%M") for t in times[lo:hi]])
    st.bar_chart(zoom, height=260, color=["#9d9d9d", "#e45756", "#54a24b"], stack=False)
    st.caption("Grouped bars, 12 hours around the worst selfish hour — the gap between red "
               "and green is what coordination is worth.")

    st.markdown("#### Act 3 — The negotiation walks the peak down")
    rounds_df = pd.DataFrame(C["rounds"]).set_index("round")[["peak_mw"]]
    rounds_df.loc["bound"] = C["joint_net"].max()
    st.bar_chart(rounds_df.rename(columns={"peak_mw": "system peak (MW)"}), height=220,
                 color=["#4c78a8"])

    bill_selfish = float(((C["p_selfish"] - C["p0"]) * C["existing_mw"]).sum())
    bill_joint = float(((C["p_joint"] - C["p0"]) * C["existing_mw"]).sum())
    l1, l2 = st.columns(2)
    l1.metric("Existing consumers pay (selfish)", f"${bill_selfish/1e6:+.1f}M",
              "over these 3 days", delta_color="inverse")
    l2.metric("Existing consumers pay (coordinated)", f"${bill_joint/1e6:+.1f}M",
              f"{(bill_joint - bill_selfish)/1e6:+.1f}M vs selfish")
    st.info("**Honest frame:** fleet sizes are projected; the grid, its prices, and its "
            "worst days are real 2026 data. System base load is held at today's level, so "
            "this isolates one variable — what synchronized flexibility does at scale. "
            "The negotiation layer is exactly where the agent strategies from the Live "
            "Labs plug in.")

# ---------------------------------------------------------------- agents
with t_agents:

    st.markdown("#### The three-level loop, on one picture")
    st.markdown("""
<svg viewBox="0 0 860 400" xmlns="http://www.w3.org/2000/svg" style="max-width:860px;width:100%">
  <defs><marker id="ar" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
    <path d="M0,0 L8,3 L0,6 z" fill="#8a8a8a"/></marker></defs>
  <rect x="20" y="20" width="820" height="88" rx="10" fill="#1f77b4" opacity="0.14"/>
  <text x="34" y="44" font-size="13" fill="#1f77b4" font-weight="bold">LEVEL 3 — STRATEGY (meta)</text>
  <rect x="330" y="34" width="200" height="60" rx="8" fill="#1f77b4"/>
  <text x="430" y="60" font-size="14" fill="#fff" text-anchor="middle" font-weight="bold">🧢 Coach (LLM)</text>
  <text x="430" y="80" font-size="11" fill="#e8f1fa" text-anchor="middle">sees only scores → refine or rethink</text>
  <rect x="20" y="140" width="820" height="88" rx="10" fill="#9467bd" opacity="0.14"/>
  <text x="34" y="164" font-size="13" fill="#9467bd" font-weight="bold">LEVEL 2 — IMPLEMENTATION</text>
  <rect x="240" y="154" width="200" height="60" rx="8" fill="#9467bd"/>
  <text x="340" y="180" font-size="14" fill="#fff" text-anchor="middle" font-weight="bold">✍️ Strategy writer (LLM)</text>
  <text x="340" y="200" font-size="11" fill="#f0eaf7" text-anchor="middle">instruction → policy class (plain Python)</text>
  <rect x="600" y="154" width="180" height="60" rx="8" fill="#d62728"/>
  <text x="690" y="180" font-size="14" fill="#fff" text-anchor="middle" font-weight="bold">🔧 Bug fixer (LLM)</text>
  <text x="690" y="200" font-size="11" fill="#fbe9e9" text-anchor="middle">traceback → repaired code (≤5 tries)</text>
  <rect x="20" y="260" width="820" height="98" rx="10" fill="#2ca02c" opacity="0.14"/>
  <text x="34" y="284" font-size="13" fill="#2ca02c" font-weight="bold">LEVEL 1 — EXECUTION (not an AI)</text>
  <rect x="290" y="276" width="280" height="66" rx="8" fill="#2ca02c"/>
  <text x="430" y="300" font-size="14" fill="#fff" text-anchor="middle" font-weight="bold">🌐 Grid twin + simulator</text>
  <text x="430" y="320" font-size="11" fill="#e9f6e9" text-anchor="middle">runs the policy hour by hour on real data,</text>
  <text x="430" y="334" font-size="11" fill="#e9f6e9" text-anchor="middle">enforces physics, returns the score</text>
  <line x1="390" y1="94" x2="345" y2="154" stroke="#8a8a8a" stroke-width="2" marker-end="url(#ar)"/>
  <text x="300" y="128" font-size="11" fill="#8a8a8a">task description</text>
  <line x1="360" y1="214" x2="405" y2="276" stroke="#8a8a8a" stroke-width="2" marker-end="url(#ar)"/>
  <text x="290" y="248" font-size="11" fill="#8a8a8a">policy code</text>
  <line x1="570" y1="290" x2="660" y2="216" stroke="#d62728" stroke-width="2" stroke-dasharray="5,4" marker-end="url(#ar)"/>
  <text x="612" y="252" font-size="11" fill="#d62728">crash</text>
  <line x1="690" y1="214" x2="690" y2="242" stroke="#d62728" stroke-width="2" stroke-dasharray="5,4"/>
  <line x1="690" y1="242" x2="576" y2="308" stroke="#d62728" stroke-width="2" stroke-dasharray="5,4" marker-end="url(#ar)"/>
  <line x1="470" y1="276" x2="470" y2="98" stroke="#2ca02c" stroke-width="2" marker-end="url(#ar)"/>
  <text x="482" y="188" font-size="11" fill="#2ca02c">score + metrics (never raw data)</text>
  <text x="430" y="382" font-size="11.5" fill="#8a8a8a" text-anchor="middle">Every arrow is a file on disk (pending_prompt.txt → response.txt) — archived verbatim, so every run is replayable. The best strategy is always kept.</text>
</svg>
""", unsafe_allow_html=True)
    st.subheader("🤝 Three agents, one loop — who talks to whom")
    st.markdown("""Every strategy you see in this demo was produced by the same conversation
between three specialists. None of them can do the job alone — and that's deliberate.""")
    st.markdown(AGENT_BOX, unsafe_allow_html=True)
    st.markdown("""
**Why the information walls matter:**
- The **coach never sees raw market data** — only aggregate scores. It can't overfit to the
  week or "cheat" by memorizing prices; it can only reason about strategy.
- The **strategy writer never sees the score history** — it gets one clean instruction per
  round. Every strategy is a fresh, auditable piece of code, not an accumulated blob.
- The **twin is not an AI** — it's a deterministic simulator calibrated on real data. The
  agents can propose anything; only the twin decides what works. AI suggests, physics disposes.
- A fourth specialist, the **🔧 bug fixer**, is summoned only when code crashes: it receives
  the error message and the broken code, and returns a repaired version (up to 5 tries).

**How the messages actually travel:** every message is a file on disk — prompts in, code and
verdicts out. That's why everything you see in this app is replayable and auditable: the
conversation *is* the record. (See 🔍 Under the hood for the verbatim transcripts.)""")

    st.markdown("#### 📜 A real conversation, reconstructed from the logs")
    conv_runs = {**dollar_runs(), **euro_runs()}
    conv_runs = {k: v for k, v in conv_runs.items() if (v[0] / "events.jsonl").exists()}
    if conv_runs:
        pick = st.selectbox("Run", list(conv_runs), key="agents_run")
        run_dir, unit, _ = conv_runs[pick]
        events = [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()]
        eps = sorted({e["episode"] for e in events})
        epk = st.selectbox("Episode", eps, key="agents_ep")
        for e in [e for e in events if e["episode"] == epk][:14]:
            if e["kind"].startswith("coach"):
                with st.chat_message("user", avatar="🧑‍🏫"):
                    st.markdown(f"**Coach → Writer** (round {e['iteration'] + 1}, "
                                f"{e['kind'].split(':')[1]} mode): {e['detail'][:280]}…")
            elif e["kind"] == "score":
                with st.chat_message("assistant", avatar="⚖️"):
                    st.markdown(f"**Twin → Coach**: {e['detail']}")
            elif e["kind"] == "crash":
                with st.chat_message("assistant", avatar="💥"):
                    st.markdown(f"**Twin → Bug fixer**: `{e['detail'][:160]}…`")
    else:
        st.info("Run a Live Lab to generate a conversation with full coach text — "
                "the original recorded runs predate full event logging.")

# ---------------------------------------------------------------- live labs
with t_live_home:
    st.subheader("🔴 Live Lab — home battery")
    live_lab({"name": "home battery (one simulated week, score in €)",
              "driver": "experiments/driver.py", "prefix": "bliv_",
              "unit": "€", "scale": 1.0, "naive_label": "No battery",
              "best_label": "Best possible", "default_ep": 2, "default_it": 6},
             "lh")

with t_live_dc:
    st.subheader("🔴 Live Lab — data center dispatch")
    live_lab({"name": "data-center dispatch problem (real market weeks, score in $M)",
              "driver": "experiments/driver2.py", "prefix": "live_",
              "unit": "$M", "scale": 1e6, "naive_label": "Do nothing",
              "best_label": "Best possible", "default_ep": 2, "default_it": 6},
             "ld")

# ---------------------------------------------------------------- under the hood
with t_hood:
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
        with st.expander("Home-battery prompts (aps/prompts.py)"):
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
            st.json({"size": "set by the story-tab slider (default 500 MW)",
                     "firm load": "50% (cannot move)",
                     "deferrable compute window": "24 h deadline, enforced",
                     "battery round-trip efficiency": "88%",
                     "battery sizing": "slider MW × 4 h of storage"})
        for run_name, label in [("results/engine_aps", "APS-over-engine run"),
                                ("results/run1", "Home-battery search run")]:
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
                    "in place. Every run started from now on keeps the full verbatim transcript.")


# ================================================================ 🎯 guided tour
ACTS = ["Welcome", "The problem", "The proof", "The fix", "The coordination",
        "The engine"]
PERSONAS = {
    "🏛 I run a city": "city",
    "🏢 I run a data center": "dc",
    "⚡ I operate the grid": "grid",
    "🔧 Show me the engineering": "eng",
}

TAKEAWAYS = {  # act -> persona -> one-line takeaway
    3: {"city": "Your residents' bills don't have to rise for the grid to grow.",
        "dc": "Flexibility cuts your energy bill ~20% **and** is your fastest path through interconnection.",
        "grid": "The interconnection request can be absorbed without firing peakers — here's the dispatch proof.",
        "eng": "The LP closes 100% of the achievable gap by construction; every other policy is scored against it."},
    4: {"city": "Coordinated flexibility protects every consumer — not just the flexible ones.",
        "dc": "Joining the coordination pool makes your interconnection case stronger than going alone.",
        "grid": "Price signals alone recover most of the coordination value — no direct control needed.",
        "eng": "Damped best-response against the repriced stack converges in a handful of rounds."},
}


def tour_ledger(step: int):
    """Persistent value strip that accumulates as the tour advances."""
    _, naive5, impact5, opt5, mit5 = run_scenario(500.0, 50, 100, True)
    items = []
    if step >= 1:
        items.append(("The problem", f"+${impact5.consumer_bill_delta/1e6:.0f}M consumer bills"))
    if step >= 2:
        _, _, twin, _ = load_twin()
        items.append(("The proof", f"twin corr {twin.report.corr:.2f} out-of-sample"))
    if step >= 3:
        cut = 1 - mit5.consumer_bill_delta / impact5.consumer_bill_delta
        items.append(("The fix", f"−{cut*100:.0f}% consumer impact"))
    if step >= 4:
        items.append(("Coordination", "herding peak eliminated"))
    if step >= 5:
        items.append(("The engine", "best strategy kept, always improving"))
    if items:
        st.markdown(" → ".join(f"**{k}**: {v}" for k, v in items))
        st.divider()


with t_tour:
    step = st.session_state.setdefault("tour_step", 0)
    persona = st.session_state.get("tour_persona", "city")

    st.progress((step + 1) / len(ACTS),
                text=f"Act {step} of {len(ACTS)-1} — {ACTS[step]}")
    tour_ledger(step)

    if step == 0:
        st.title("How much new load can this grid welcome?")
        st.caption("One year of real market data · NYISO day-ahead · every number out-of-sample")
        p = st.radio("Who's asking?", list(PERSONAS), horizontal=True, key="tour_who")
        st.session_state["tour_persona"] = PERSONAS[p]
        mw = st.slider("Drop a new data center on the grid (MW)", 100, 1500, 500, 100,
                       key="tour_mw")
        _, naive_t, impact_t, _, _ = run_scenario(float(mw), 50, 100, False)
        m1, m2, m3 = st.columns(3)
        m1.metric("Peak clearing-price impact", f"+${impact_t.peak_price_delta:.0f}/MWh",
                  "every consumer pays this hour", delta_color="inverse")
        m2.metric("Consumer bill impact (2 weeks)",
                  f"+${impact_t.consumer_bill_delta/1e6:.0f}M",
                  "before any intervention", delta_color="inverse")
        m3.metric("Its own energy bill", f"${naive_t.energy_cost/1e6:.1f}M")
        st.markdown("**That's the problem.** The next four screens show the proof, "
                    "the fix, the coordination — and the engine that keeps improving it.")

    elif step == 1:
        df, test, twin, iso = load_twin()
        st.header("Electricity is an auction — the priciest plant sets everyone's price")
        daily = df.set_index("time")["LMP"].resample("D").agg(["median", "max"])
        st.line_chart(daily.rename(columns={"median": "typical day ($/MWh)",
                                            "max": "worst hour ($/MWh)"}), height=260)
        c1, c2, c3 = st.columns(3)
        c1.metric("Typical price", f"${df['LMP'].median():.0f}/MWh")
        c2.metric("Worst hour this year", f"${df['LMP'].max():.0f}/MWh",
                  f"{df['LMP'].max()/df['LMP'].median():.0f}x typical", delta_color="inverse")
        c3.metric("Avg carbon intensity", f"{df['carbon_t_per_mwh'].mean()*1000:.0f} kg/MWh")
        st.markdown("Scarcity hours are rare — but they set the year's economics. "
                    "**New demand that lands in those hours is what raises everyone's bills.**")
        st.caption(f"{iso} day-ahead prices, {YEAR_START} → {YEAR_END}. Real data, no simulation.")

    elif step == 2:
        df, test, twin, iso = load_twin()
        st.header("First, earn the right to say “what if” — the digital twin")
        pred = twin.predict(test["net_load_mw"].values, test["time"])
        st.line_chart(pd.DataFrame({"actual price": test["LMP"].values,
                                    "twin's price": pred},
                                   index=test["time"]), height=260)
        c1, c2, c3 = st.columns(3)
        c1.metric("Correlation (held-out)", f"{twin.report.corr:.2f}")
        c2.metric("Typical error", f"${twin.report.mae:.0f}/MWh")
        c3.metric("Trained on", f"{twin.report.n_train:,} hours")
        st.markdown("The twin rebuilds the market's supply curve from public data — and is "
                    "graded on **weeks it never saw**. Only because it tracks reality do the "
                    "counterfactuals that follow mean anything.")
        st.caption("Isotonic merit-order reconstruction; scarcity hours are where the error concentrates — shown, not hidden.")

    elif step == 3:
        st.header("The fix: dispatch the flexibility the load already has")
        fx1, fx2 = st.columns(2)
        flex = fx1.slider("Share of compute that can wait up to 24 h", 0, 80, 50, 10,
                          key="tour_flex", format="%d%%")
        batt = fx2.slider("Battery size (MW)", 0, 300, 100, 50, key="tour_batt")
        _, naive_f, impact_f, opt_f, mit_f = run_scenario(500.0, flex, batt, True)
        l1, l2, l3 = st.columns(3)
        l1.metric("Do nothing", f"+${impact_f.consumer_bill_delta/1e6:.0f}M",
                  "consumer bills", delta_color="off")
        l2.metric("Dispatch its flexibility", f"+${mit_f.consumer_bill_delta/1e6:.0f}M",
                  f"−{(1-mit_f.consumer_bill_delta/impact_f.consumer_bill_delta)*100:.0f}% consumer impact")
        l3.metric("Its own bill", f"${opt_f.energy_cost/1e6:.1f}M",
                  f"−{(1-opt_f.energy_cost/naive_f.energy_cost)*100:.0f}% vs rigid")
        st.markdown(f"**{TAKEAWAYS[3][st.session_state.get('tour_persona','city')]}**")
        st.caption("Optimal lookahead dispatch (LP) on the calibrated twin; same compute served, different hours.")

    elif step == 4:
        st.header("One grid, many good intentions — why coordination is the product")
        co = run_coordination(50_000, 500, 0, 4)
        st.line_chart(pd.DataFrame({
            "baseline net load (MW)": co["base"],
            "everyone selfish (herding)": co["selfish_net"],
            "coordinated": co["joint_net"]}, index=co["times"]), height=280)
        r1, r2, r3 = st.columns(3)
        r1.metric("Selfish peak", f"{co['selfish_net'].max():,.0f} MW",
                  f"+{co['selfish_net'].max()-co['base'].max():,.0f} MW rebound",
                  delta_color="inverse")
        r2.metric("Coordinated peak", f"{co['joint_net'].max():,.0f} MW",
                  f"{co['joint_net'].max()-co['base'].max():+,.0f} MW vs baseline")
        r3.metric("Negotiation", f"{len(co['rounds'])} rounds",
                  f"peak ${co['rounds'][0]['peak_price']:.0f} → ${co['rounds'][-1]['peak_price']:.0f}/MWh")
        st.markdown(f"**{TAKEAWAYS[4][st.session_state.get('tour_persona','city')]}**")
        st.caption("50,000 home batteries + the 500 MW data center on the year's worst 3 days; three solved regimes.")

    elif step == 5:
        st.header("The engine keeps improving — and shows its work")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Do nothing", "$14.9M", "added system cost", delta_color="off")
        s2.metric("AI-written rules", "$14.1M", "closed 27% of the gap")
        s3.metric("Hand-written rules", "$13.3M", "closed 55%")
        s4.metric("Optimizer (bound)", "$12.0M", "100% — kept in the loop")
        st.markdown("""
AI agents **write strategies as plain code**, a simulator scores them, a coach decides
*refine or rethink* — and the best strategy is always kept. Where classical optimization
wins, the scoreboard says so; that honesty is the design.
**Next:** open a 🔴 Live Lab to watch a search happen, or 🔍 Under the hood for every
prompt and transcript.""")
        st.caption("Recorded searches; every round, prompt, and score archived and replayable in this app.")

    nav_l, _, nav_r = st.columns([1, 4, 1])
    if step > 0 and nav_l.button("← Back", key="tour_back"):
        st.session_state["tour_step"] = step - 1
        st.rerun()
    if step < len(ACTS) - 1 and nav_r.button("Next →", type="primary", key="tour_next"):
        st.session_state["tour_step"] = step + 1
        st.rerun()


# ================================================================ 🛰 mission control
with t_mission:
    df_m, test_m, twin_m, iso_m = load_twin()
    ctrl, center, ledger = st.columns([1, 3, 1], gap="medium")

    with ctrl:
        st.markdown("##### Scenario")
        mc_mw = st.slider("Data center (MW)", 100, 1000, 500, 100, key="mc_mw")
        mc_flex = st.slider("Deferrable compute", 0, 80, 50, 10, key="mc_flex", format="%d%%")
        mc_batt = st.slider("DC battery (MW)", 0, 300, 100, 50, key="mc_batt")
        mc_homes = st.select_slider("Home batteries", [10_000, 25_000, 50_000, 100_000],
                                    50_000, key="mc_homes")
        mc_spike = st.slider("Heat-wave severity (+MW)", 0, 2000, 0, 500, key="mc_spike")
        st.caption(f"{iso_m} · twin corr {twin_m.report.corr:.2f} · out-of-sample")

    _, mc_naive, mc_imp, mc_opt, mc_mit = run_scenario(float(mc_mw), mc_flex, mc_batt, True)
    mc_co = run_coordination(mc_homes, mc_mw, mc_spike, 4)

    with center:
        st.markdown("##### The worst 3 days — net load under three futures")
        st.line_chart(pd.DataFrame({
            "baseline (MW)": mc_co["base"],
            "selfish flexibility (herding)": mc_co["selfish_net"],
            "coordinated": mc_co["joint_net"]}, index=mc_co["times"]), height=250)
        st.markdown("##### Prices those futures produce")
        st.line_chart(pd.DataFrame({
            "baseline ($/MWh)": mc_co["p0"],
            "selfish": mc_co["p_selfish"],
            "coordinated": mc_co["p_joint"]}, index=mc_co["times"]), height=180)

    with ledger:
        st.markdown("##### Ledger")
        st.metric("Peak price impact", f"+${mc_imp.peak_price_delta:.0f}/MWh",
                  "if rigid", delta_color="inverse")
        st.metric("Consumer bills", f"+${mc_imp.consumer_bill_delta/1e6:.0f}M",
                  f"→ +${mc_mit.consumer_bill_delta/1e6:.0f}M dispatched")
        st.metric("DC bill saving", f"{(1-mc_opt.energy_cost/mc_naive.energy_cost)*100:.0f}%")
        st.metric("Herding rebound", f"+{mc_co['selfish_net'].max()-mc_co['base'].max():,.0f} MW",
                  "erased when coordinated", delta_color="inverse")
        carbon = (mc_opt.served_mw * test_m["carbon_t_per_mwh"].values).sum()
        st.metric("DC carbon (dispatched)", f"{carbon:,.0f} tCO₂")

    st.divider()
    feed_c, board_c = st.columns([3, 2])
    with feed_c:
        st.markdown("##### Latest agent activity")
        ev_files = sorted(ROOT.glob("results/*/events.jsonl"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        if ev_files:
            events = [json.loads(l) for l in ev_files[0].read_text().splitlines()][-6:]
            for e in events:
                icon = {"score": "🎯", "crash": "💥", "spike": "⚡"}.get(
                    e["kind"].split(":")[0], "🗣")
                st.markdown(f"{icon} `{e['episode']}` · round {e['iteration']} — "
                            f"{e['detail'][:140]}")
            st.caption(f"from {ev_files[0].parent.name} — open a 🔴 Live Lab to add to it")
        else:
            st.info("No recorded agent activity yet — start a run in a 🔴 Live Lab.")
    with board_c:
        st.markdown("##### Strategy scoreboard (recorded)")
        st.markdown("""
| Policy | Added system cost | Gap closed |
|---|---|---|
| Do nothing | $14.9M | — |
| **AI-written rules** | **$14.1M** | **27%** |
| Hand-written rules | $13.3M | 55% |
| Optimizer (bound) | $12.0M | 100% |""")


# ---------------------------------------------------------------- today (calm)
with t_today:
    st.subheader("🧘 Today's grid — the calm, honest story")
    st.markdown("""
Run **today's actual fleet sizes** through the same three regimes and the drama vanishes —
and that's the finding, not a failure: **at 2026 scale, the overnight valley is so deep that
the grid absorbs everyone's flexibility, selfish or coordinated.**""")

    Ct = run_coordination(50_000, 500, 0, 4)
    times_t = pd.to_datetime(Ct["times"].values) if hasattr(Ct["times"], "values") else Ct["times"]

    c1, c2, c3 = st.columns(3)
    c1.metric("What all actors add at the peak",
              f"{(Ct['selfish_net'] - Ct['base'])[np.argmax(Ct['base'])]:+,.0f} MW",
              "on a ~20,000 MW system")
    c2.metric("Peak price change", f"${Ct['p_selfish'].max() - Ct['p0'].max():+,.0f}/MWh",
              "selfish vs baseline")
    c3.metric("Overnight headroom", f"{Ct['base'].max() - Ct['base'].min():,.0f} MW",
              "the valley everyone recharges into")

    st.markdown("#### Everything they add fits in the valley")
    fit_df = pd.DataFrame({
        "Grid load (MW)": Ct["base"],
        "All flexibility, selfish (MW added)": Ct["selfish_net"] - Ct["base"],
    }, index=times_t)
    st.bar_chart(fit_df, height=280, color=["#9d9d9d", "#e45756"])
    st.caption("Gray bars: the real grid over its worst 3 days. Red bars: everything "
               "today's data center + 50,000 home batteries add or shift. The red never "
               "reaches the gray peaks — it hides in the valley.")

    st.success("**Why this matters:** (1) Today, interconnection fear is mostly about *rigid* "
               "load at the peak — flexible load is easy to absorb. (2) The herding problem "
               "is real but *ahead of us* — open the 🌊 2035 tab to see when it arrives. "
               "Coordination is infrastructure you build *before* you need it.")


# ---------------------------------------------------------------- who pays
@st.cache_data(show_spinner="Dispatching the data center across the whole year…")
def year_smoothing(mw: float, flex_pct: int, batt_mw: int):
    df, _, twin, _ = load_twin()
    yr = df.iloc[-8760:].reset_index(drop=True) if len(df) > 8760 else df
    dc = datacenter(len(yr), mw=mw, deferrable_frac=flex_pct / 100,
                    battery_mwh=batt_mw * 4.0, battery_mw=float(batt_mw))
    rigid = assess(twin, yr, dc)
    opt = dispatch_pfa(twin, yr, dc)
    disp = assess(twin, yr, dc, injected_mw=opt.served_mw)
    out = pd.DataFrame({"date": yr["time"].dt.date,
                        "Grid alone": rigid.price_base,
                        "Rigid data center": rigid.price_new,
                        "Dispatched data center": disp.price_new})
    daily = out.groupby("date").max()
    return daily

with t_who:
    st.subheader("⚖️ Who pays? Two neighborhoods and one data center")
    era_w = st.radio("Scale", ["Today (50k battery homes, 500 MW DC)",
                               "2035 (1M battery homes, 3 GW DC, 4M EVs)"],
                     horizontal=True)
    if era_w.startswith("Today"):
        W = run_coordination(50_000, 500, 0, 4); n_b = 50_000
    else:
        W = run_coordination(1_000_000, 3_000, 0, 5, 4_000_000); n_b = 1_000_000
    home_mwh_mo = 0.7      # typical NY household consumption per month [MWh]
    scale_mo = 10.0        # 3-day episode -> per-month (~x10)
    ex_mwh = float(W["existing_mw"].sum())
    up_self = float(((W["p_selfish"] - W["p0"]) * W["existing_mw"]).sum()) / ex_mwh
    up_joint = float(((W["p_joint"] - W["p0"]) * W["existing_mw"]).sum()) / ex_mwh
    fleet_rev = max(float((-W["fleet_batt"] * W["p_joint"]).sum()), 0.0) / n_b
    r1, r2, r3 = st.columns(3)
    r1.metric("🏚 Home WITHOUT battery",
              f"{up_self * home_mwh_mo * scale_mo:+.2f} $/mo",
              f"{up_joint * home_mwh_mo * scale_mo:+.2f} $/mo if coordinated",
              delta_color="inverse" if up_joint > 0 else "normal")
    r2.metric("🔋 Home WITH battery",
              f"{up_self * home_mwh_mo * scale_mo - fleet_rev * scale_mo:+.2f} $/mo",
              f"earns ${fleet_rev * scale_mo:.2f}/mo arbitrage")
    r3.metric("🏢 The data center",
              "hedged by its own flexibility",
              "see story tab 3 for its bill")
    st.caption("Price uplift × household consumption, scaled from the worst-3-day episode "
               "to a month (×10) — an upper-bound month, labeled as such. Battery homes "
               "feel the same uplift but earn arbitrage; batteryless homes just pay. "
               "Coordination is what protects the second group.")

    st.markdown("#### The whole year — which peaks got smoothed")
    yd = year_smoothing(500.0, 50, 100)
    st.line_chart(yd, height=280, color=["#9d9d9d", "#e45756", "#54a24b"])
    shave = (yd["Rigid data center"] - yd["Dispatched data center"])
    smoothed = shave[shave > 10].sort_values(ascending=False)
    c1, c2 = st.columns([1, 2])
    c1.metric("Spike days smoothed", f"{len(smoothed)}",
              f"best day: −${shave.max():.0f}/MWh off the peak")
    with c2:
        st.bar_chart(smoothed.head(15).rename("peak $/MWh shaved"), height=220,
                     color=["#54a24b"])
    st.caption("Daily peak prices across the entire dataset: gray = grid alone, red = a rigid "
               "500 MW data center, green = the same data center dispatched by threshold rules "
               "(the fast policy — the LP does better still). Bars: the 15 days where dispatch "
               "shaved the most off the peak.")
