"""Globally optimal benchmarks with perfect foresight (paper Appendix D).

Deterministic LP over the full horizon with variables c_t, d_t (charge /
discharge power) and i_t, e_t (grid import / export power):

    min  sum_t (i_t * p_t^buy - e_t * p_t^sell) * dt
    s.t. R_{t+1} = R_t + eta*c_t*dt - (1/eta)*d_t*dt,   0 <= R_t <= R_max
         P_t^pv + d_t + i_t = L_t + c_t + e_t
         0 <= c_t <= C_max, 0 <= d_t <= D_max, i_t, e_t >= 0

Finite-horizon optimum: R_0 fixed, R_T free.
Steady-state optimum:   additionally R_T = R_0.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

from .simulation import ExogenousSeries, SystemParams


@dataclass
class BenchmarkResult:
    total_cost: float
    charge_kw: np.ndarray
    discharge_kw: np.ndarray
    import_kw: np.ndarray
    export_kw: np.ndarray
    soc_kwh: np.ndarray


def solve_optimal(
    series: ExogenousSeries, params: SystemParams, steady_state: bool = False
) -> BenchmarkResult:
    T = params.horizon
    dt = params.dt_h
    eta = params.eta_leg
    R0 = params.initial_soc * params.battery_capacity_kwh

    # Variable layout: [c_0..c_{T-1}, d_0.., i_0.., e_0..]
    n = 4 * T
    c_off, d_off, i_off, e_off = 0, T, 2 * T, 3 * T

    cost = np.zeros(n)
    cost[i_off:i_off + T] = series.buy_price * dt
    cost[e_off:e_off + T] = -series.sell_price * dt

    # Power balance equalities: i - e - c + d = L - P^pv
    A_eq = lil_matrix((T + (1 if steady_state else 0), n))
    b_eq = np.zeros(T + (1 if steady_state else 0))
    for t in range(T):
        A_eq[t, i_off + t] = 1.0
        A_eq[t, e_off + t] = -1.0
        A_eq[t, c_off + t] = -1.0
        A_eq[t, d_off + t] = 1.0
        b_eq[t] = series.load_kw[t] - series.pv_kw[t]

    # SoC bounds: R_k = R0 + sum_{t<k} (eta*c_t - d_t/eta)*dt for k = 1..T
    A_ub = lil_matrix((2 * T, n))
    b_ub = np.zeros(2 * T)
    for k in range(1, T + 1):
        for t in range(k):
            A_ub[k - 1, c_off + t] = eta * dt          #  R_k <= R_max
            A_ub[k - 1, d_off + t] = -dt / eta
            A_ub[T + k - 1, c_off + t] = -eta * dt     # -R_k <= 0
            A_ub[T + k - 1, d_off + t] = dt / eta
        b_ub[k - 1] = params.battery_capacity_kwh - R0
        b_ub[T + k - 1] = R0

    if steady_state:  # R_T = R_0
        for t in range(T):
            A_eq[T, c_off + t] = eta * dt
            A_eq[T, d_off + t] = -dt / eta
        b_eq[T] = 0.0

    bounds = (
        [(0.0, params.max_charge_kw)] * T
        + [(0.0, params.max_discharge_kw)] * T
        + [(0.0, None)] * 2 * T
    )

    res = linprog(
        cost, A_ub=A_ub.tocsr(), b_ub=b_ub, A_eq=A_eq.tocsr(), b_eq=b_eq,
        bounds=bounds, method="highs",
    )
    if not res.success:
        raise RuntimeError(f"LP failed: {res.message}")

    c = res.x[c_off:c_off + T]
    d = res.x[d_off:d_off + T]
    soc = np.concatenate([[R0], R0 + np.cumsum(eta * c * dt - d * dt / eta)])
    return BenchmarkResult(
        total_cost=float(res.fun),
        charge_kw=c,
        discharge_kw=d,
        import_kw=res.x[i_off:i_off + T],
        export_kw=res.x[e_off:e_off + T],
        soc_kwh=soc,
    )
