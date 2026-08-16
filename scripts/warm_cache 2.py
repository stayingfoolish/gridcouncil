"""Warm every expensive cached loader the app relies on, and write the
landing-dial defaults so the Tour's first paint needs zero computation.

    .venv/bin/python scripts/warm_cache.py

Imports app.py in bare mode (its Streamlit calls are no-ops outside a
server session), calls each cached loader once with the app's default
arguments, prints timings, and writes results/landing_defaults.json.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def timed(label, fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    print(f"{label:<55s} {time.perf_counter() - t0:7.2f} s")
    return out


def main():
    t_all = time.perf_counter()
    print("importing app.py (runs the page once, bare mode)…")
    t0 = time.perf_counter()
    import app  # noqa: E402  (executes the page script; warms load_twin)
    print(f"{'import app':<55s} {time.perf_counter() - t0:7.2f} s")

    timed("load_twin()", app.load_twin)
    _, naive, impact, _, _ = timed(
        "run_scenario(500.0, 50, 100, True)", app.run_scenario, 500.0, 50, 100, True)
    _, naive_f, impact_f, _, _ = timed(
        "run_scenario(500.0, 50, 100, False)", app.run_scenario, 500.0, 50, 100, False)
    timed("run_coordination(50_000, 500, 0, 4)",
          app.run_coordination, 50_000, 500, 0, 4)

    # Landing-dial defaults: exactly what Tour Act 0 shows at mw=500
    # (run_scenario with use_lookahead=False, per the tour slider path).
    defaults = {
        "mw": 500,
        "peak_price_delta": float(impact_f.peak_price_delta),
        "consumer_bill_delta": float(impact_f.consumer_bill_delta),
        "energy_cost": float(naive_f.energy_cost),
    }
    out = ROOT / "results" / "landing_defaults.json"
    out.write_text(json.dumps(defaults, indent=2))
    print(f"wrote {out.relative_to(ROOT)}: {defaults}")
    print(f"{'TOTAL':<55s} {time.perf_counter() - t_all:7.2f} s")


if __name__ == "__main__":
    main()
