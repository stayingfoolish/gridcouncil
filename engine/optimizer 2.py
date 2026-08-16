"""Optimizer: dispatch the new load's flexibility (deferrable jobs + storage)
against the twin.

Two policy classes from the sequential-decision framework are implemented and
arbitrated by realized system cost:
  PFA - threshold rules: defer above a price threshold, recover below;
        battery price-band charging (cheap, robust, no lookahead).
  DLA - direct lookahead / MPC: an LP over the horizon minimizing the convex
        system dispatch cost implied by the merit-order stack (the twin's
        monotone supply curve integrates to a piecewise-linear convex cost),
        so the optimizer explicitly avoids setting a higher marginal price.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

from .scenario import NewLoad


@dataclass
class DispatchResult:
    policy: str
    served_mw: np.ndarray        # grid draw of the new load after flexibility
    battery_mw: np.ndarray       # >0 charge, <0 discharge
    deferred_backlog: np.ndarray
    system_cost_delta: float     # added system dispatch cost vs no new load
    energy_cost: float           # what the new load pays at resulting prices
    peak_price_delta: float


def _eta(load: NewLoad) -> float:
    return float(np.sqrt(load.battery_rt_eff))


def evaluate(twin, frame, load: NewLoad, served: np.ndarray, policy: str) -> DispatchResult:
    base_net = frame["net_load_mw"].values
    p0 = twin.predict(base_net, frame["time"])
    p1 = twin.predict(base_net + served, frame["time"])
    # system dispatch cost delta = integral of stack between the two net loads
    seg_x, seg_p = twin.grid, twin.grid_price
    cum = np.concatenate([[0.0], np.cumsum(0.5 * (seg_p[1:] + seg_p[:-1]) * np.diff(seg_x))])
    def C(x):
        return np.interp(x, seg_x, cum)
    delta = float((C(base_net + served) - C(base_net)).sum())
    return DispatchResult(
        policy=policy, served_mw=served, battery_mw=np.zeros_like(served),
        deferred_backlog=np.zeros_like(served),
        system_cost_delta=delta,
        energy_cost=float((p1 * served).sum()),
        peak_price_delta=float((p1 - p0).max()),
    )


# ---------------- PFA: threshold policy ----------------

def dispatch_pfa(twin, frame, load: NewLoad,
                 defer_pct: float = 75.0, band_pct=(25.0, 80.0)) -> DispatchResult:
    base_net = frame["net_load_mw"].values
    p_base = twin.predict(base_net, frame["time"])
    tau = np.percentile(p_base, defer_pct)
    p_lo, p_hi = np.percentile(p_base, band_pct[0]), np.percentile(p_base, band_pct[1])
    eta = _eta(load)

    T = len(base_net)
    firm = load.profile_mw * (1 - load.deferrable_frac)
    flex = load.profile_mw * load.deferrable_frac
    served = np.zeros(T); backlog = np.zeros(T); batt = np.zeros(T)
    b = 0.0; soc = load.battery_mwh / 2
    for t in range(T):
        draw = firm[t]
        if p_base[t] > tau:
            b += flex[t]                       # defer
        else:
            draw += flex[t]
            catchup = min(b, float(load.profile_mw.max()))   # recover backlog, bounded
            draw += catchup; b -= catchup
        # drop backlog older than window (kept simple: cap backlog age via decay)
        x = 0.0
        if load.battery_mwh > 0:
            if p_base[t] <= p_lo and soc < load.battery_mwh:
                x = min(load.battery_mw, (load.battery_mwh - soc) / eta)
            elif p_base[t] >= p_hi and soc > 0:
                x = -min(load.battery_mw, soc * eta, draw)
            soc += eta * max(x, 0) - max(-x, 0) / eta
        batt[t] = x
        served[t] = max(draw + x, 0.0)
        backlog[t] = b
    res = evaluate(twin, frame, load, served, "PFA threshold rules")
    res.battery_mw = batt; res.deferred_backlog = backlog
    return res


# ---------------- DLA: lookahead LP ----------------

def dispatch_dla(twin, frame, load: NewLoad, n_seg: int = 40) -> DispatchResult:
    """Minimize total system dispatch cost over the horizon subject to the
    load's flexibility: energy conservation of deferred jobs within the
    window, battery dynamics, capacity limits. Convex stack -> LP."""
    base_net = frame["net_load_mw"].values
    T = len(base_net)
    eta = _eta(load)
    firm = load.profile_mw * (1 - load.deferrable_frac)
    flex = load.profile_mw * load.deferrable_frac
    W = load.defer_window_h

    seg_x, seg_p = twin.system_cost_segments(n_seg)
    # variables: s_t (flexible served), c_t, d_t (battery), z_{t,k} segment MW
    # net draw_t = firm + s_t + c_t - d_t ; sum_k z_{t,k} = base_net + draw_t
    n_z = T * (n_seg - 1)
    idx_s, idx_c, idx_d, idx_z = 0, T, 2 * T, 3 * T
    n = 3 * T + n_z

    cost = np.zeros(n)
    widths = np.diff(seg_x)
    for t in range(T):
        cost[idx_z + t * (n_seg - 1): idx_z + (t + 1) * (n_seg - 1)] = seg_p[:-1]

    A_eq = lil_matrix((T + 1, n)); b_eq = np.zeros(T + 1)
    for t in range(T):
        row = t
        for k in range(n_seg - 1):
            A_eq[row, idx_z + t * (n_seg - 1) + k] = 1.0
        A_eq[row, idx_s + t] = -1.0
        A_eq[row, idx_c + t] = -1.0
        A_eq[row, idx_d + t] = 1.0
        b_eq[row] = base_net[t] + firm[t]
    # all flexible energy served over horizon; window enforced via A_ub below
    A_eq[T, idx_s:idx_s + T] = 1.0
    b_eq[T] = flex.sum()

    rows = []
    # deferral window: cumulative served >= cumulative arrivals shifted by W
    A_ub = lil_matrix((2 * T + T, n)); b_ub = np.zeros(3 * T)
    r = 0
    for t in range(T):
        A_ub[r, idx_s:idx_s + t + 1] = -1.0
        b_ub[r] = -flex[:max(t + 1 - W, 0)].sum()
        r += 1
    # cumulative served <= cumulative arrivals (no serving jobs early)
    for t in range(T):
        A_ub[r, idx_s:idx_s + t + 1] = 1.0
        b_ub[r] = flex[:t + 1].sum()
        r += 1
    # battery SoC bounds: 0 <= soc0 + sum(eta*c - d/eta) <= E
    for t in range(T):
        A_ub[r, idx_c:idx_c + t + 1] = eta
        A_ub[r, idx_d:idx_d + t + 1] = -1.0 / eta
        b_ub[r] = load.battery_mwh / 2 * 0 + (load.battery_mwh - load.battery_mwh / 2)
        r += 1

    # SoC lower bound needs another T rows
    A_lb = lil_matrix((T, n)); b_lb = np.zeros(T)
    for t in range(T):
        A_lb[t, idx_c:idx_c + t + 1] = -eta
        A_lb[t, idx_d:idx_d + t + 1] = 1.0 / eta
        b_lb[t] = load.battery_mwh / 2
    from scipy.sparse import vstack
    A_ub_all = vstack([A_ub.tocsr(), A_lb.tocsr()])
    b_ub_all = np.concatenate([b_ub, b_lb])

    bounds = ([(0, 3 * load.profile_mw.max())] * T          # s_t
              + [(0, load.battery_mw)] * 2 * T              # c_t, d_t
              + [(0, w) for _ in range(T) for w in widths]) # z segments

    res = linprog(cost, A_ub=A_ub_all, b_ub=b_ub_all,
                  A_eq=A_eq.tocsr(), b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"DLA LP failed: {res.message}")
    s = res.x[idx_s:idx_s + T]
    c = res.x[idx_c:idx_c + 2 * T][:T]
    d = res.x[idx_d:idx_d + T]
    served = np.maximum(firm + s + c - d, 0.0)
    out = evaluate(twin, frame, load, served, "DLA lookahead (MPC)")
    out.battery_mw = c - d
    out.deferred_backlog = np.maximum(np.cumsum(flex) - np.cumsum(s), 0.0)
    return out


def arbitrate(twin, frame, load: NewLoad) -> dict:
    """Run both policy classes plus the naive baseline; pick by realized
    system cost (the multi-agent arbitration of v1)."""
    naive = evaluate(twin, frame, load, load.profile_mw, "naive (inflexible)")
    pfa = dispatch_pfa(twin, frame, load)
    dla = dispatch_dla(twin, frame, load)
    best = min([pfa, dla], key=lambda r: r.system_cost_delta)
    return {"naive": naive, "pfa": pfa, "dla": dla, "best": best}
