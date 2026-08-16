"""Post-processing of LLM output into executable code and sandboxed
policy evaluation in an isolated subprocess."""

import json
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .simulation import SystemParams

_REPO_ROOT = Path(__file__).resolve().parent.parent

_EVAL_SCRIPT = textwrap.dedent(
    """
    import json, sys, time, traceback

    sys.path.insert(0, {repo!r})
    from aps.simulation import SystemParams, generate_exogenous, simulate

    code_path, params_json = sys.argv[1], sys.argv[2]
    params = SystemParams(**json.loads(params_json))
    series = generate_exogenous(params)
    source = open(code_path).read()

    try:
        namespace = {{}}
        exec(compile(source, "policy.py", "exec"), namespace)
        policy_cls = namespace.get("Policy")
        if policy_cls is None:
            raise NameError("generated code does not define a class named 'Policy'")
        policy = policy_cls()
        t0 = time.perf_counter()
        result = simulate(policy, series, params)
        runtime = time.perf_counter() - t0
    except Exception:
        tb = traceback.format_exc(limit=6)
        print(json.dumps({{"ok": False, "error": tb[-2000:]}}))
        sys.exit(0)

    print(json.dumps({{
        "ok": True,
        "total_cost": result.total_cost,
        "utilization_pct": result.utilization_pct,
        "avg_soc_pct": result.avg_soc_pct,
        "runtime_s": runtime,
        "soc_kwh": result.soc_kwh.tolist(),
        "actions_kw": result.actions_kw.tolist(),
        "cost_per_step": result.cost_per_step.tolist(),
    }}))
    """
)


def postprocess(raw: str) -> str:
    """Phi: strip markdown fences / prose and return executable policy code."""
    text = raw.strip()
    fences = re.findall(r"```(?:python)?\n(.*?)```", text, flags=re.DOTALL)
    if fences:
        text = max(fences, key=len)  # largest fenced block
    lines = text.splitlines()
    start = 0
    for idx, line in enumerate(lines):
        if re.match(r"^(import\s|from\s+\S+\s+import|class\s+Policy\b)", line):
            start = idx
            break
    return "\n".join(lines[start:]).strip() + "\n"


@dataclass
class EvalOutcome:
    ok: bool
    error: str = ""
    total_cost: float = float("nan")
    utilization_pct: float = 0.0
    avg_soc_pct: float = 0.0
    runtime_s: float = 0.0
    soc_kwh: list = None
    actions_kw: list = None
    cost_per_step: list = None


def evaluate_policy_code(
    code: str, params: SystemParams, workdir: Path, timeout_s: float = 120.0
) -> EvalOutcome:
    """Execute the generated policy over the full horizon in an isolated
    subprocess; runtime errors are captured and returned for the repair loop."""
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    code_path = workdir / "candidate_policy.py"
    code_path.write_text(code)
    script_path = workdir / "_eval_runner.py"
    script_path.write_text(_EVAL_SCRIPT.format(repo=str(_REPO_ROOT)))
    params_json = json.dumps(params.__dict__)

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), str(code_path), params_json],
            capture_output=True, text=True, timeout=timeout_s, cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return EvalOutcome(ok=False, error=f"Policy evaluation timed out after {timeout_s:.0f}s")

    stdout = proc.stdout.strip().splitlines()
    payload = None
    for line in reversed(stdout):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        err = (proc.stderr or "no output from evaluation subprocess")[-2000:]
        return EvalOutcome(ok=False, error=err)
    if not payload.get("ok"):
        return EvalOutcome(ok=False, error=payload.get("error", "unknown error"))
    return EvalOutcome(
        ok=True,
        total_cost=payload["total_cost"],
        utilization_pct=payload["utilization_pct"],
        avg_soc_pct=payload["avg_soc_pct"],
        runtime_s=payload["runtime_s"],
        soc_kwh=payload["soc_kwh"],
        actions_kw=payload["actions_kw"],
        cost_per_step=payload["cost_per_step"],
    )
