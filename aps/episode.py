"""Episode state machine implementing Algorithm 1 (paper Appendix A).

Each episode runs I meta-iterations. Every iteration: the meta-policy emits a
task_description (greedy refine/explore with stagnation switching, Section
7.3.1), the generation level turns it into a Policy class (with up to 5
repair attempts on failure, Section 7.2.2), and the policy is evaluated in
the simulation (Level 1).

The LLM is accessed asynchronously through the filesystem: when an episode
needs an LLM completion it writes `pending_prompt.txt` and pauses; an
external worker writes `response.txt` and the driver advances the state.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .policy_runtime import evaluate_policy_code, postprocess
from .simulation import SystemParams

MAX_REPAIR_ATTEMPTS = 5     # paper: restart iteration after 5 failed repairs
MAX_RESTARTS = 2            # bound on iteration restarts (practical cap)
STAGNATION_WINDOW = 3       # iterations without improvement -> explore
IMPROVEMENT_EPS = 0.05      # minimum cost improvement counted as progress [eur]


@dataclass
class IterationRecord:
    iteration: int
    total_cost: float
    utilization_pct: float
    avg_soc_pct: float
    runtime_s: float
    repair_attempts: int
    restarts: int
    mode: str                     # refine / explore / initial
    code_file: str


@dataclass
class EpisodeState:
    episode_dir: Path
    params: SystemParams
    n_iterations: int = 10
    phase: str = "gen_pending"    # gen_pending | repair_pending | meta_pending | done
    iteration: int = 0
    repair_attempts: int = 0
    restarts: int = 0
    task_description: str = prompts.INITIAL_TASK_DESCRIPTION
    mode: str = "initial"
    best_cost: float = float("inf")
    best_iteration: int = -1
    last_code: str = ""
    last_metrics: dict = field(default_factory=dict)
    cost_history: list = field(default_factory=list)
    records: list = field(default_factory=list)
    price_volatility_pct: float = 0.0

    # ---------------- persistence ----------------

    def save(self) -> None:
        state = {k: v for k, v in self.__dict__.items() if k not in ("episode_dir", "params")}
        state["records"] = [r.__dict__ for r in self.records]
        (self.episode_dir / "state.json").write_text(json.dumps(state, indent=2))

    @classmethod
    def load(cls, episode_dir: Path, params: SystemParams) -> "EpisodeState":
        data = json.loads((episode_dir / "state.json").read_text())
        records = [IterationRecord(**r) for r in data.pop("records")]
        st = cls(episode_dir=episode_dir, params=params)
        st.__dict__.update(data)
        st.records = records
        return st

    # ---------------- prompt construction ----------------

    def current_prompt(self) -> str:
        if self.phase == "gen_pending":
            return prompts.GENERATION_PROMPT.format(
                policy_signature=prompts.POLICY_SIGNATURE,
                task_description=self.task_description,
            )
        if self.phase == "repair_pending":
            return prompts.REPAIR_PROMPT.format(
                error_message=self.last_metrics.get("error", "unknown error"),
                policy_code=self.last_code,
                policy_signature=prompts.POLICY_SIGNATURE,
            )
        if self.phase == "meta_pending":
            improved = self.cost_history and min(
                self.cost_history[:-1], default=float("inf")
            ) - self.cost_history[-1] > IMPROVEMENT_EPS
            recent = self.cost_history[-STAGNATION_WINDOW:]
            stagnating = (
                len(self.cost_history) > STAGNATION_WINDOW
                and min(self.cost_history[:-STAGNATION_WINDOW]) - min(recent) <= IMPROVEMENT_EPS
            )
            refine = improved and not stagnating
            self.mode = "refine" if refine else "explore"
            return prompts.META_PROMPT.format(
                total_cost=f"{self.cost_history[-1]:.2f}",
                best_cost=f"{self.best_cost:.2f}",
                iteration_count=self.iteration,
                utilization=f"{self.last_metrics.get('utilization_pct', 0.0):.1f}",
                avg_soc=f"{self.last_metrics.get('avg_soc_pct', 0.0):.1f}",
                price_volatility=f"{self.price_volatility_pct:.1f}",
                cost_history=[round(c, 2) for c in self.cost_history[-5:]],
                policy_code=self.last_code,
                explore_or_refine_instruction=(
                    prompts.REFINE_INSTRUCTION if refine else prompts.EXPLORE_INSTRUCTION
                ),
                task_mode=(
                    prompts.REFINE_TASK_MODE if refine else prompts.EXPLORE_TASK_MODE
                ),
            )
        raise RuntimeError(f"no prompt for phase {self.phase}")

    def write_pending_prompt(self) -> None:
        (self.episode_dir / "pending_prompt.txt").write_text(self.current_prompt())
        (self.episode_dir / "phase.txt").write_text(self.phase)

    # ---------------- response processing ----------------

    def process_response(self, response: str) -> str:
        """Advance the state machine with an LLM response. Returns a short
        human-readable event description."""
        if self.phase in ("gen_pending", "repair_pending"):
            return self._process_code_response(response)
        if self.phase == "meta_pending":
            self.task_description = response.strip()
            self.phase = "gen_pending"
            return f"iter {self.iteration}: meta -> {self.mode} instruction ({len(response)} chars)"
        raise RuntimeError(f"unexpected response in phase {self.phase}")

    def _process_code_response(self, response: str) -> str:
        code = postprocess(response)
        outcome = evaluate_policy_code(code, self.params, self.episode_dir / "work")
        if not outcome.ok:
            self.last_code = code
            self.last_metrics = {"error": outcome.error}
            self.repair_attempts += 1
            if self.repair_attempts >= MAX_REPAIR_ATTEMPTS:
                if self.restarts >= MAX_RESTARTS:
                    return self._record_failed_iteration(code)
                self.restarts += 1
                self.repair_attempts = 0
                self.phase = "gen_pending"   # restart iteration (paper 7.2.2)
                return f"iter {self.iteration}: {MAX_REPAIR_ATTEMPTS} repairs failed -> restart {self.restarts}"
            self.phase = "repair_pending"
            return f"iter {self.iteration}: eval error -> repair attempt {self.repair_attempts}"

        # success: record the iteration
        code_file = f"iter_{self.iteration:02d}_policy.py"
        (self.episode_dir / code_file).write_text(code)
        rec = IterationRecord(
            iteration=self.iteration,
            total_cost=outcome.total_cost,
            utilization_pct=outcome.utilization_pct,
            avg_soc_pct=outcome.avg_soc_pct,
            runtime_s=outcome.runtime_s,
            repair_attempts=self.repair_attempts,
            restarts=self.restarts,
            mode=self.mode,
            code_file=code_file,
        )
        self.records.append(rec)
        self.cost_history.append(outcome.total_cost)
        if outcome.total_cost < self.best_cost:
            self.best_cost = outcome.total_cost
            self.best_iteration = self.iteration
            (self.episode_dir / "best_trace.json").write_text(json.dumps({
                "iteration": self.iteration,
                "total_cost": outcome.total_cost,
                "soc_kwh": outcome.soc_kwh,
                "actions_kw": outcome.actions_kw,
                "cost_per_step": outcome.cost_per_step,
            }))
        self.last_code = code
        self.last_metrics = {
            "utilization_pct": outcome.utilization_pct,
            "avg_soc_pct": outcome.avg_soc_pct,
        }
        msg = (
            f"iter {self.iteration}: cost {outcome.total_cost:.2f} "
            f"(best {self.best_cost:.2f}, {self.repair_attempts} repairs)"
        )
        self.repair_attempts = 0
        self.restarts = 0
        self.iteration += 1
        self.phase = "done" if self.iteration >= self.n_iterations else "meta_pending"
        return msg

    def _record_failed_iteration(self, code: str) -> str:
        """All repairs and restarts exhausted: record a no-op iteration so the
        episode can continue (documented deviation from the paper, which
        restarts indefinitely)."""
        rec = IterationRecord(
            iteration=self.iteration,
            total_cost=float("nan"),
            utilization_pct=0.0,
            avg_soc_pct=0.0,
            runtime_s=0.0,
            repair_attempts=self.repair_attempts,
            restarts=self.restarts,
            mode=self.mode,
            code_file="",
        )
        self.records.append(rec)
        self.repair_attempts = 0
        self.restarts = 0
        self.iteration += 1
        self.phase = "done" if self.iteration >= self.n_iterations else "meta_pending"
        return f"iter {self.iteration - 1}: FAILED permanently, recorded as NaN"
