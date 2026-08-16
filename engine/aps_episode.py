"""Episode state machine for APS-over-engine (adapted from aps/episode.py).

Same phases and filesystem protocol as the home-battery search, so the same
LLM-worker wave pattern drives it: pending_prompt.txt -> response.txt ->
driver step. The objective is the added system dispatch cost in $ (lower is
better; the DLA LP bound is included in the meta prompt as calibration)."""

import json
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from aps.policy_runtime import postprocess
from engine import aps_dispatch as P

MAX_REPAIR_ATTEMPTS = 5
MAX_RESTARTS = 2
STAGNATION_WINDOW = 3
IMPROVEMENT_EPS = 10_000.0        # $ improvement that counts as progress

_REPO = Path(__file__).resolve().parent.parent

_EVAL_SCRIPT = textwrap.dedent(
    """
    import json, pickle, sys, traceback
    sys.path.insert(0, {repo!r})
    from engine.aps_dispatch import simulate_dispatch

    code_path, env_path = sys.argv[1], sys.argv[2]
    env = pickle.load(open(env_path, "rb"))
    source = open(code_path).read()
    try:
        ns = {{}}
        exec(compile(source, "policy.py", "exec"), ns)
        cls = ns.get("DispatchPolicy")
        if cls is None:
            raise NameError("generated code does not define class 'DispatchPolicy'")
        out = simulate_dispatch(cls(), env)
    except Exception:
        print(json.dumps({{"ok": False, "error": traceback.format_exc(limit=6)[-2000:]}}))
        sys.exit(0)
    print(json.dumps({{
        "ok": True, "system_cost_delta": out.system_cost_delta,
        "energy_cost": out.energy_cost, "peak_price_delta": out.peak_price_delta,
        "forced_mwh": out.forced_mwh, "battery_util_pct": out.battery_util_pct,
        "served_mw": out.served_mw, "battery_mw": out.battery_mw,
        "backlog_mwh": out.backlog_mwh, "reasons": out.reasons or [],
    }}))
    """
)


def evaluate_code(code: str, run_dir: Path, workdir: Path, timeout_s: float = 180.0) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "candidate_policy.py").write_text(code)
    script = workdir / "_eval.py"
    script.write_text(_EVAL_SCRIPT.format(repo=str(_REPO)))
    try:
        proc = subprocess.run(
            [sys.executable, str(script), str(workdir / "candidate_policy.py"),
             str(run_dir / "env.pkl")],
            capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"evaluation timed out after {timeout_s:.0f}s"}
    for line in reversed(proc.stdout.strip().splitlines() or [""]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"ok": False, "error": (proc.stderr or "no output")[-2000:]}


@dataclass
class EngineEpisode:
    episode_dir: Path
    run_dir: Path
    lp_bound: float
    naive_cost: float
    n_iterations: int = 10
    phase: str = "gen_pending"
    iteration: int = 0
    repair_attempts: int = 0
    restarts: int = 0
    task_description: str = P.INITIAL_TASK_DESCRIPTION
    mode: str = "initial"
    best_cost: float = float("inf")
    best_iteration: int = -1
    last_code: str = ""
    last_metrics: dict = field(default_factory=dict)
    cost_history: list = field(default_factory=list)
    records: list = field(default_factory=list)

    def save(self):
        st = {k: v for k, v in self.__dict__.items()
              if k not in ("episode_dir", "run_dir")}
        (self.episode_dir / "state.json").write_text(json.dumps(st, indent=2))

    @classmethod
    def load(cls, episode_dir: Path, run_dir: Path) -> "EngineEpisode":
        data = json.loads((episode_dir / "state.json").read_text())
        ep = cls(episode_dir=episode_dir, run_dir=run_dir,
                 lp_bound=data["lp_bound"], naive_cost=data["naive_cost"])
        ep.__dict__.update(data)
        return ep

    def current_prompt(self) -> str:
        if self.phase == "gen_pending":
            return P.GENERATION_PROMPT.format(
                policy_signature=P.POLICY_SIGNATURE,
                task_description=self.task_description)
        if self.phase == "repair_pending":
            return P.REPAIR_PROMPT.format(
                error_message=self.last_metrics.get("error", "unknown"),
                policy_code=self.last_code,
                policy_signature=P.POLICY_SIGNATURE)
        if self.phase == "meta_pending":
            improved = self.cost_history and min(
                self.cost_history[:-1], default=float("inf")
            ) - self.cost_history[-1] > IMPROVEMENT_EPS
            recent = self.cost_history[-STAGNATION_WINDOW:]
            stagnating = (len(self.cost_history) > STAGNATION_WINDOW and
                          min(self.cost_history[:-STAGNATION_WINDOW]) - min(recent)
                          <= IMPROVEMENT_EPS)
            refine = improved and not stagnating
            self.mode = "refine" if refine else "explore"
            m = self.last_metrics
            return P.META_PROMPT.format(
                total_cost=f"{self.cost_history[-1]:,.0f}",
                best_cost=f"{self.best_cost:,.0f}",
                lp_bound=f"{self.lp_bound:,.0f}",
                naive_cost=f"{self.naive_cost:,.0f}",
                iteration_count=self.iteration,
                energy_cost=f"{m.get('energy_cost', 0):,.0f}",
                peak_delta=f"{m.get('peak_price_delta', 0):.1f}",
                forced_mwh=f"{m.get('forced_mwh', 0):,.0f}",
                battery_util=f"{m.get('battery_util_pct', 0):.1f}",
                cost_history=[round(c) for c in self.cost_history[-5:]],
                policy_code=self.last_code,
                explore_or_refine_instruction=(P.REFINE_INSTRUCTION if refine
                                               else P.EXPLORE_INSTRUCTION),
                task_mode=(P.REFINE_TASK_MODE if refine else P.EXPLORE_TASK_MODE))
        raise RuntimeError(f"no prompt for phase {self.phase}")

    def write_pending_prompt(self):
        (self.episode_dir / "pending_prompt.txt").write_text(self.current_prompt())
        (self.episode_dir / "phase.txt").write_text(self.phase)

    def _log_event(self, kind: str, detail: str, value: float | None = None) -> None:
        with open(self.run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"episode": self.episode_dir.name,
                                "iteration": self.iteration,
                                "kind": kind, "detail": detail[:600],
                                "value": value}) + "\n")

    def process_response(self, response: str) -> str:
        if self.phase == "meta_pending":
            self.task_description = response.strip()
            self.phase = "gen_pending"
            self._log_event(f"coach:{self.mode}", response.strip())
            return f"iter {self.iteration}: meta -> {self.mode} ({len(response)} chars)"
        code = postprocess(response)
        out = evaluate_code(code, self.run_dir, self.episode_dir / "work")
        if not out.get("ok"):
            self.last_code = code
            self.last_metrics = {"error": out.get("error", "unknown")}
            self.repair_attempts += 1
            if self.repair_attempts >= MAX_REPAIR_ATTEMPTS:
                if self.restarts >= MAX_RESTARTS:
                    self.records.append({"iteration": self.iteration,
                                         "system_cost_delta": None,
                                         "mode": self.mode, "failed": True})
                    self.repair_attempts = 0; self.restarts = 0
                    self.iteration += 1
                    self.phase = "done" if self.iteration >= self.n_iterations else "meta_pending"
                    return f"iter {self.iteration-1}: FAILED permanently"
                self.restarts += 1; self.repair_attempts = 0
                self.phase = "gen_pending"
                return f"iter {self.iteration}: 5 repairs failed -> restart {self.restarts}"
            self.phase = "repair_pending"
            self._log_event("crash", out.get("error", "")[-300:])
            return f"iter {self.iteration}: eval error -> repair {self.repair_attempts}"

        cost = out["system_cost_delta"]
        code_file = f"iter_{self.iteration:02d}_policy.py"
        (self.episode_dir / code_file).write_text(code)
        self.records.append({
            "iteration": self.iteration, "system_cost_delta": cost,
            "energy_cost": out["energy_cost"],
            "peak_price_delta": out["peak_price_delta"],
            "forced_mwh": out["forced_mwh"],
            "battery_util_pct": out["battery_util_pct"],
            "repair_attempts": self.repair_attempts, "mode": self.mode,
            "code_file": code_file})
        self.cost_history.append(cost)
        if cost < self.best_cost:
            self.best_cost = cost; self.best_iteration = self.iteration
            (self.episode_dir / "best_trace.json").write_text(json.dumps({
                "iteration": self.iteration, "system_cost_delta": cost,
                "served_mw": out["served_mw"], "battery_mw": out["battery_mw"],
                "backlog_mwh": out["backlog_mwh"],
                "reasons": out.get("reasons", [])}))
        self.last_code = code
        self.last_metrics = {k: out[k] for k in
                             ("energy_cost", "peak_price_delta", "forced_mwh",
                              "battery_util_pct")}
        msg = (f"iter {self.iteration}: syscost ${cost:,.0f} "
               f"(best ${self.best_cost:,.0f}, LP ${self.lp_bound:,.0f})")
        self._log_event("score", msg, value=cost)
        self.repair_attempts = 0; self.restarts = 0
        self.iteration += 1
        self.phase = "done" if self.iteration >= self.n_iterations else "meta_pending"
        return msg
