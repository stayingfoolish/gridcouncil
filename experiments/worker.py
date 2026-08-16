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


def complete_with_local(prompt: str, model: str, base_url: str, api_key: str) -> str:
    """Any OpenAI-compatible endpoint (DGX Spark via NIM / vLLM / Ollama…)."""
    import requests
    r = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "max_tokens": 4096,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=600)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def complete_with_cli(prompt: str, model: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "text"],
        input=prompt, capture_output=True, text=True, timeout=300, env=env)
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr.strip() or proc.stdout.strip()
                  or f"claude exited {proc.returncode} with no output")
        raise RuntimeError(f"claude CLI: {detail[-500:]}")
    return proc.stdout


CFG = {"backend": "auto", "base_url": "", "api_key": "local"}


def backend() -> str:
    if CFG["backend"] != "auto":
        return CFG["backend"]
    return "anthropic-api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-cli"


def complete(prompt: str, model: str) -> str:
    b = backend()
    if b == "local":
        return complete_with_local(prompt, model, CFG["base_url"], CFG["api_key"])
    if b == "anthropic-api":
        return complete_with_api(prompt, model)
    return complete_with_cli(prompt, model)


def preflight(model: str) -> None:
    """One tiny test call with loud, actionable failure output."""
    b = backend()
    label = {"anthropic-api": "Anthropic API", "claude-cli": "claude CLI",
             "local": f"local OpenAI-compatible endpoint {CFG['base_url']}"}[b]
    print(f"backend: {label} · model: {model}")
    try:
        out = complete("Reply with exactly: OK", model)
        print(f"preflight OK ({out.strip()[:20]!r})")
    except Exception as e:
        print(f"\nPREFLIGHT FAILED: {e!r}\n")
        if b == "anthropic-api":
            print("Fix: check ANTHROPIC_API_KEY is valid and has credit, e.g.\n"
                  "  curl https://api.anthropic.com/v1/models "
                  '-H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01"')
        elif b == "local":
            print(f"Fix: check the endpoint is up and serving the model:\n"
                  f"  curl {CFG['base_url'].rstrip('/')}/models\n"
                  "and that --model matches a served model name exactly.")
        else:
            print("Fix: either  export ANTHROPIC_API_KEY=sk-ant-…  or log the CLI in:\n"
                  "  claude  (then /login)\n"
                  "Note: an EMPTY ANTHROPIC_API_KEY ('export ANTHROPIC_API_KEY=') routes "
                  "to the CLI — run `unset ANTHROPIC_API_KEY` if you meant the CLI.")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--driver", default="experiments/driver2.py")
    ap.add_argument("--backend", default=os.environ.get("LLM_BACKEND", "auto"),
                    choices=["auto", "anthropic-api", "claude-cli", "local"],
                    help="auto (default) = today's behavior: Anthropic API if "
                         "ANTHROPIC_API_KEY is set, else the claude CLI. "
                         "local = any OpenAI-compatible endpoint (DGX Spark).")
    ap.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL",
                    "http://localhost:8000/v1"))
    ap.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", "local"))
    args = ap.parse_args()
    CFG.update(backend=args.backend, base_url=args.base_url, api_key=args.api_key)
    run_dir = Path(args.run)
    preflight(args.model)

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
                (run_dir / "worker.pid").unlink(missing_ok=True)
                return
            time.sleep(1)
            continue
        for p in pending:
            ep = p.parent
            print(f"[{ep.name}] serving {(ep / 'phase.txt').read_text().strip()} …", flush=True)
            try:
                response = complete(p.read_text(), args.model)
            except Exception as e:
                print(f"  LLM call failed: {e!r}; retrying in 10s")
                time.sleep(10)
                continue
            (ep / "response.txt").write_text(response)
        if step():
            print("all episodes complete")
            return


if __name__ == "__main__":
    main()
