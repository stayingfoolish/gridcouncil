"""Standalone LLM worker for live APS runs.

Run from YOUR terminal (where `claude` is authenticated, or with
ANTHROPIC_API_KEY set):

    .venv/bin/python experiments/driver2.py init --run results/engine_live --episodes 3 --iterations 8
    .venv/bin/python experiments/worker.py --run results/engine_live

The worker loops: serve every pending_prompt.txt with an LLM completion,
advance the state machine (driver step), repeat until all episodes finish.
The Streamlit app's Live mode tails the same directory, so you can watch the
deliberation happen round by round while this runs.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def complete_with_api(prompt: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=4096,
        messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if b.type == "text")


def complete_with_cli(prompt: str, model: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "text"],
        input=prompt, capture_output=True, text=True, timeout=300, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:])
    return proc.stdout


def complete(prompt: str, model: str) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return complete_with_api(prompt, model)
    return complete_with_cli(prompt, model)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--driver", default="experiments/driver2.py")
    args = ap.parse_args()
    run_dir = Path(args.run)

    def step() -> bool:
        """Advance the state machine; True when all episodes are done."""
        out = subprocess.run(
            [sys.executable, args.driver, "step", "--run", args.run],
            capture_output=True, text=True).stdout.strip()
        for line in out.splitlines():
            print(" ", line, flush=True)
        done_line = out.splitlines()[-1]           # "DONE x/y"
        finished, total = done_line.replace("DONE", "").strip().split("/")
        return finished == total

    while True:
        pending = sorted(run_dir.glob("episode_*/pending_prompt.txt"))
        if not pending:
            if step():
                print("all episodes complete")
                return
            time.sleep(1)
            continue
        for p in pending:
            ep = p.parent
            print(f"[{ep.name}] serving {(ep / 'phase.txt').read_text().strip()} …", flush=True)
            try:
                response = complete(p.read_text(), args.model)
            except Exception as e:
                print(f"  LLM call failed: {e}; retrying in 10s")
                time.sleep(10)
                continue
            (ep / "response.txt").write_text(response)
        if step():
            print("all episodes complete")
            return


if __name__ == "__main__":
    main()
