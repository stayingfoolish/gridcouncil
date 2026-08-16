"""Inject a demand spike into a live policy-search run, mid-flight.

    python experiments/inject.py --run results/live_XXXX [--mw 800] [--hours 24]
    python experiments/inject.py --run results/bliv_XXXX [--price-mult 1.6]

Both drivers re-read their environment on every evaluation, so the very next
scored round faces the new world: scores jump, the coach sees the history
worsen, and the search must adapt. The event is logged so the app's live feed
(and later replays) can mark the moment the world changed.
"""

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np


def log_event(run_dir: Path, detail: str):
    with open(run_dir / "events.jsonl", "a") as f:
        f.write(json.dumps({"episode": "environment", "iteration": -1,
                            "kind": "spike", "detail": detail, "value": None}) + "\n")


def inject_engine(run_dir: Path, mw: float, hours: int):
    env_path = run_dir / "env.pkl"
    env = pickle.load(open(env_path, "rb"))
    base = env["base_net"]
    T = len(base)
    # center the spike on the highest-load stretch still ahead of a typical run
    peak = int(np.argmax(base))
    a, b = max(0, peak - hours // 2), min(T, peak + hours // 2)
    spike = np.zeros(T)
    spike[a:b] = mw * np.hanning(b - a)          # smooth ramp up/down
    p_before = np.interp(base, env["stack_x"], env["stack_price"])
    p_after = np.interp(base + spike, env["stack_x"], env["stack_price"])
    env["base_net"] = base + spike
    env["p0"] = env["p0"] + (p_after - p_before)
    with open(env_path, "wb") as f:
        pickle.dump(env, f)
    log_event(run_dir, f"DEMAND SPIKE injected: +{mw:.0f} MW for {hours}h around the "
                       f"system peak (hours {a}-{b}); prices rose up to "
                       f"+${(p_after - p_before).max():.0f}/MWh. All future rounds are "
                       "scored against this harder world; earlier benchmarks predate it.")
    print(f"engine spike: +{mw:.0f} MW over hours {a}-{b}, "
          f"max price impact +${(p_after - p_before).max():.0f}/MWh")


def inject_battery(run_dir: Path, price_mult: float):
    cfg_path = run_dir / "config.json"
    cfg = json.loads(cfg_path.read_text())
    p = cfg["params"]
    p["grid_buy_price"] = round(p["grid_buy_price"] * price_mult, 4)
    p["price_peak_amplitude"] = round(p["price_peak_amplitude"] * price_mult, 4)
    cfg_path.write_text(json.dumps(cfg, indent=2))
    log_event(run_dir, f"PRICE SURGE injected: grid buy price multiplied by "
                       f"{price_mult}x (now {p['grid_buy_price']} EUR/kWh). All future "
                       "rounds are scored against this harder world; earlier "
                       "benchmarks predate it.")
    print(f"battery surge: buy price x{price_mult} -> {p['grid_buy_price']} EUR/kWh")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--mw", type=float, default=800.0)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--price-mult", type=float, default=1.6)
    args = ap.parse_args()
    run_dir = Path(args.run)
    if (run_dir / "env.pkl").exists():
        inject_engine(run_dir, args.mw, args.hours)
    elif (run_dir / "config.json").exists():
        inject_battery(run_dir, args.price_mult)
    else:
        sys.exit(f"{run_dir}: no env.pkl or config.json found")


if __name__ == "__main__":
    main()
