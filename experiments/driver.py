"""Filesystem-based experiment driver for the APS replication.

Usage:
  python experiments/driver.py init  --run results/run1 --episodes 10 --iterations 10
  python experiments/driver.py step  --run results/run1     # ingest responses, emit prompts
  python experiments/driver.py status --run results/run1

Protocol per episode directory (results/<run>/episode_XX/):
  pending_prompt.txt  prompt awaiting an LLM completion (phase in phase.txt)
  response.txt        completion written by the LLM worker
`step` consumes response.txt, advances the state machine, and writes the next
pending_prompt.txt (or marks the episode done).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aps.episode import EpisodeState
from aps.simulation import SystemParams, generate_exogenous


def episode_dirs(run_dir: Path):
    return sorted(run_dir.glob("episode_*"))


def cmd_init(args):
    run_dir = Path(args.run)
    run_dir.mkdir(parents=True, exist_ok=True)
    params = SystemParams()
    series = generate_exogenous(params)
    (run_dir / "config.json").write_text(json.dumps(
        {"episodes": args.episodes, "iterations": args.iterations,
         "params": params.__dict__}, indent=2))
    for e in range(1, args.episodes + 1):
        ep_dir = run_dir / f"episode_{e:02d}"
        ep_dir.mkdir(exist_ok=True)
        st = EpisodeState(episode_dir=ep_dir, params=params,
                          n_iterations=args.iterations)
        st.price_volatility_pct = series.price_volatility_pct
        st.write_pending_prompt()
        st.save()
    print(f"initialized {args.episodes} episodes x {args.iterations} iterations in {run_dir}")


def load_params(run_dir: Path) -> SystemParams:
    cfg = json.loads((run_dir / "config.json").read_text())
    return SystemParams(**cfg["params"])


def cmd_step(args):
    run_dir = Path(args.run)
    params = load_params(run_dir)
    pending, done = [], 0
    for ep_dir in episode_dirs(run_dir):
        st = EpisodeState.load(ep_dir, params)
        resp_path = ep_dir / "response.txt"
        if st.phase != "done" and resp_path.exists():
            response = resp_path.read_text()
            resp_path.unlink()
            (ep_dir / "pending_prompt.txt").unlink(missing_ok=True)
            event = st.process_response(response)
            print(f"{ep_dir.name}: {event}")
            if st.phase != "done":
                st.write_pending_prompt()
            else:
                (ep_dir / "phase.txt").write_text("done")
            st.save()
        if st.phase == "done":
            done += 1
        elif (ep_dir / "pending_prompt.txt").exists():
            pending.append(f"{ep_dir.name}:{st.phase}")
    print(f"PENDING {len(pending)} :: {' '.join(pending)}")
    print(f"DONE {done}/{len(episode_dirs(run_dir))}")


def cmd_status(args):
    run_dir = Path(args.run)
    params = load_params(run_dir)
    for ep_dir in episode_dirs(run_dir):
        st = EpisodeState.load(ep_dir, params)
        costs = [f"{r.total_cost:.2f}" for r in st.records]
        print(f"{ep_dir.name} phase={st.phase} iter={st.iteration} "
              f"best={st.best_cost:.2f} costs=[{', '.join(costs)}]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--run", required=True)
    p_init.add_argument("--episodes", type=int, default=10)
    p_init.add_argument("--iterations", type=int, default=10)
    p_init.set_defaults(func=cmd_init)
    for name, fn in (("step", cmd_step), ("status", cmd_status)):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.set_defaults(func=fn)
    args = ap.parse_args()
    args.func(args)
