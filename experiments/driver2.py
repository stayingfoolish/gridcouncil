"""Driver for APS-over-engine dispatch policy search (same protocol as driver.py)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.aps_episode import EngineEpisode


def episode_dirs(run_dir: Path):
    return sorted(run_dir.glob("episode_*"))


def cmd_init(args):
    run_dir = Path(args.run)
    run_dir.mkdir(parents=True, exist_ok=True)

    from engine.aps_dispatch import build_env
    build_env(run_dir)

    # baselines on the identical configuration
    from engine.data import fetch
    from engine.optimizer import arbitrate
    from engine.scenario import datacenter
    from engine.twin import MeritOrderTwin
    df, _ = fetch("2026-06-01", "2026-08-14")
    df = df.sort_values("time").reset_index(drop=True)
    twin = MeritOrderTwin.calibrate(df)
    test = df.iloc[-twin.report.n_test:].reset_index(drop=True)
    dc = datacenter(len(test), mw=500.0, deferrable_frac=0.5,
                    battery_mwh=400.0, battery_mw=100.0)
    res = arbitrate(twin, test, dc)
    baselines = {k: round(v.system_cost_delta) for k, v in res.items() if k != "best"}
    (run_dir / "baselines.json").write_text(json.dumps(baselines, indent=2))

    for e in range(1, args.episodes + 1):
        ep_dir = run_dir / f"episode_{e:02d}"
        ep_dir.mkdir(exist_ok=True)
        ep = EngineEpisode(episode_dir=ep_dir, run_dir=run_dir,
                           lp_bound=baselines["dla"], naive_cost=baselines["naive"],
                           n_iterations=args.iterations)
        ep.write_pending_prompt()
        ep.save()
    print("baselines:", baselines)
    print(f"initialized {args.episodes} episodes x {args.iterations} iterations in {run_dir}")


def cmd_step(args):
    run_dir = Path(args.run)
    pending, done = [], 0
    for ep_dir in episode_dirs(run_dir):
        ep = EngineEpisode.load(ep_dir, run_dir)
        resp = ep_dir / "response.txt"
        if ep.phase != "done" and resp.exists():
            response = resp.read_text()
            # archive the full exchange verbatim before consuming it
            tdir = ep_dir / "transcript"
            tdir.mkdir(exist_ok=True)
            seq = len(list(tdir.glob("*.response.txt")))
            phase = ep.phase
            prompt_file = ep_dir / "pending_prompt.txt"
            if prompt_file.exists():
                (tdir / f"{seq:03d}_{phase}.prompt.txt").write_text(prompt_file.read_text())
            (tdir / f"{seq:03d}_{phase}.response.txt").write_text(response)
            resp.unlink()
            (ep_dir / "pending_prompt.txt").unlink(missing_ok=True)
            print(f"{ep_dir.name}: {ep.process_response(response)}")
            if ep.phase != "done":
                ep.write_pending_prompt()
            else:
                (ep_dir / "phase.txt").write_text("done")
            ep.save()
        if ep.phase == "done":
            done += 1
        elif (ep_dir / "pending_prompt.txt").exists():
            pending.append(f"{ep_dir.name}:{ep.phase}")
    print(f"PENDING {len(pending)} :: {' '.join(pending)}")
    print(f"DONE {done}/{len(episode_dirs(run_dir))}")


def cmd_status(args):
    run_dir = Path(args.run)
    for ep_dir in episode_dirs(run_dir):
        ep = EngineEpisode.load(ep_dir, run_dir)
        costs = [f"{r['system_cost_delta']/1e6:.2f}M" if r.get("system_cost_delta")
                 else "FAIL" for r in ep.records]
        print(f"{ep_dir.name} phase={ep.phase} iter={ep.iteration} "
              f"best={ep.best_cost/1e6:.2f}M costs=[{', '.join(costs)}]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--run", required=True)
    p.add_argument("--episodes", type=int, default=5)
    p.add_argument("--iterations", type=int, default=10)
    p.set_defaults(func=cmd_init)
    for name, fn in (("step", cmd_step), ("status", cmd_status)):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.set_defaults(func=fn)
    args = ap.parse_args()
    args.func(args)
