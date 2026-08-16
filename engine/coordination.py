"""Coordination scenario: a household battery fleet and a data center acting
on the same grid during a demand spike.

Three regimes on identical physics:
  selfish()      each actor optimizes against the base price forecast as a
                 price-taker -> herding: everyone discharges into the same
                 peak and recharges in the same trough, creating a rebound
  negotiate()    price-signal iteration: a coordinator re-prices the system
                 after each round of selfish responses (damped) — the visible
                 form of dual decomposition; converges toward coordination
  joint()        one LP over both actors minimizing true system (stack) cost
                 with everyone's constraints — the coordinated bound

All three reuse the twin's convex merit-order stack, so costs and prices are
consistent with the rest of the engine.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix, vstack


@dataclass
class Actor:
    name: str
    firm_mw: np.ndarray            # inflexible draw per hour
    flex_mw: np.ndarray            # deferrable arrivals per hour (24h deadline)
    defer_window_h: int
    batt_mwh: float
    batt_mw: float
    eta: float                     # per-leg efficiency
    max_serve_mw: float = 0.0      # cap on flex service rate (0 = auto)


def dc_actor(T: int, mw: float = 500.0, flex_frac: float = 0.5,
             batt_mwh: float = 400.0, batt_mw: float = 100.0) -> Actor:
    return Actor("data center", np.full(T, mw * (1 - flex_frac)),
                 np.full(T, mw * flex_frac), 24, batt_mwh, batt_mw,
                 float(np.sqrt(0.88)))


def fleet_actor(T: int, n_homes: int = 50_000, batt_kwh: float = 13.5,
                batt_kw: float = 5.0) -> Actor:
    """Aggregated home-battery fleet. Household base load is already inside
    the system load; the fleet contributes battery dispatch only."""
    return Actor(f"{n_homes:,} home batteries", np.zeros(T), np.zeros(T), 24,
                 n_homes * batt_kwh / 1000.0, n_homes * batt_kw / 1000.0,
                 float(np.sqrt(0.90)))


def ev_actor(T: int, n_evs: int, hours: np.ndarray,
             kwh_per_day: float = 10.0, window_h: int = 12) -> Actor:
    """Aggregated EV fleet: each day's charging energy arrives as deferrable
    load at 18:00 and must be served within the overnight window."""
    flex = np.zeros(T)
    flex[np.asarray(hours) == 18] = n_evs * kwh_per_day / 1000.0  # MWh -> MW-h
    # diversified fleet charging capacity: ~2 kW average per plugged-in EV
    return Actor(f"{n_evs:,} EVs", np.zeros(T), flex, window_h, 0.0, 0.0, 1.0,
                 max_serve_mw=n_evs * 2.0 / 1000.0)


@dataclass
class Dispatch:
    actor: str
    draw_mw: np.ndarray            # net grid draw added by the actor (can be <0)
    battery_mw: np.ndarray


def price_taker_lp(actor: Actor, prices: np.ndarray) -> Dispatch:
    """The actor's selfish best response to a fixed price series: minimize its
    own bill subject to its constraints (deferral deadline, battery limits)."""
    T = len(prices)
    eta = actor.eta
    # variables: s_t (flex served), c_t, d_t (battery)
    n = 3 * T
    idx_s, idx_c, idx_d = 0, T, 2 * T
    cost = np.concatenate([prices, prices, -prices]).astype(float)
    # tiny throughput penalty keeps the battery from cycling on ties
    cost[idx_c:idx_c + T] += 0.01
    cost[idx_d:idx_d + T] += 0.01

    A_eq = lil_matrix((1, n)); b_eq = np.array([actor.flex_mw.sum()])
    A_eq[0, idx_s:idx_s + T] = 1.0

    rows = []
    A_ub = lil_matrix((4 * T, n)); b_ub = np.zeros(4 * T)
    r = 0
    W = actor.defer_window_h
    cum_flex = np.cumsum(actor.flex_mw)
    for t in range(T):
        A_ub[r, idx_s:idx_s + t + 1] = -1.0            # deadline
        b_ub[r] = -(cum_flex[t - W] if t >= W else 0.0)
        r += 1
    for t in range(T):
        A_ub[r, idx_s:idx_s + t + 1] = 1.0             # can't serve early
        b_ub[r] = cum_flex[t]
        r += 1
    for t in range(T):                                  # SoC <= E
        A_ub[r, idx_c:idx_c + t + 1] = eta
        A_ub[r, idx_d:idx_d + t + 1] = -1.0 / eta
        b_ub[r] = actor.batt_mwh / 2.0
        r += 1
    for t in range(T):                                  # SoC >= 0
        A_ub[r, idx_c:idx_c + t + 1] = -eta
        A_ub[r, idx_d:idx_d + t + 1] = 1.0 / eta
        b_ub[r] = actor.batt_mwh / 2.0
        r += 1

    s_cap = actor.max_serve_mw or max(actor.flex_mw.max() * 4, 1e-6)
    bounds = ([(0, float(s_cap))] * T
              + [(0, actor.batt_mw)] * 2 * T)
    res = linprog(cost, A_ub=A_ub.tocsr(), b_ub=b_ub, A_eq=A_eq.tocsr(),
                  b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"price-taker LP failed for {actor.name}: {res.message}")
    s = res.x[idx_s:idx_s + T]
    c = res.x[idx_c:idx_c + 2 * T][:T]
    d = res.x[idx_d:idx_d + T]
    return Dispatch(actor.name, actor.firm_mw + s + c - d, c - d)


def joint_lp(actors: list, base_net: np.ndarray, stack_x: np.ndarray,
             stack_price: np.ndarray, n_seg: int = 40) -> list:
    """Coordinated bound: minimize true system stack cost over all actors."""
    T = len(base_net)
    seg_idx = np.linspace(0, len(stack_x) - 1, n_seg).astype(int)
    sx, sp = stack_x[seg_idx], stack_price[seg_idx]
    widths = np.diff(sx)
    nz = T * (n_seg - 1)
    per = 3 * T
    n = per * len(actors) + nz
    z0 = per * len(actors)

    cost = np.zeros(n)
    for t in range(T):
        cost[z0 + t * (n_seg - 1): z0 + (t + 1) * (n_seg - 1)] = sp[:-1]
    for ai in range(len(actors)):
        o = ai * per
        cost[o + T:o + 3 * T] += 0.01              # battery throughput tie-break

    A_eq = lil_matrix((T + len(actors), n))
    b_eq = np.zeros(T + len(actors))
    for t in range(T):
        for k in range(n_seg - 1):
            A_eq[t, z0 + t * (n_seg - 1) + k] = 1.0
        b_eq[t] = base_net[t]
        for ai, a in enumerate(actors):
            o = ai * per
            A_eq[t, o + t] = -1.0                  # s_t
            A_eq[t, o + T + t] = -1.0              # c_t
            A_eq[t, o + 2 * T + t] = 1.0           # d_t
            b_eq[t] += a.firm_mw[t]
    for ai, a in enumerate(actors):
        A_eq[T + ai, ai * per: ai * per + T] = 1.0
        b_eq[T + ai] = a.flex_mw.sum()

    blocks, rhs = [], []
    for ai, a in enumerate(actors):
        o = ai * per
        W = a.defer_window_h
        cum_flex = np.cumsum(a.flex_mw)
        A = lil_matrix((4 * T, n)); b = np.zeros(4 * T)
        r = 0
        for t in range(T):
            A[r, o:o + t + 1] = -1.0
            b[r] = -(cum_flex[t - W] if t >= W else 0.0); r += 1
        for t in range(T):
            A[r, o:o + t + 1] = 1.0
            b[r] = cum_flex[t]; r += 1
        for t in range(T):
            A[r, o + T:o + T + t + 1] = a.eta
            A[r, o + 2 * T:o + 2 * T + t + 1] = -1.0 / a.eta
            b[r] = a.batt_mwh / 2.0; r += 1
        for t in range(T):
            A[r, o + T:o + T + t + 1] = -a.eta
            A[r, o + 2 * T:o + 2 * T + t + 1] = 1.0 / a.eta
            b[r] = a.batt_mwh / 2.0; r += 1
        blocks.append(A.tocsr()); rhs.append(b)

    bounds = []
    for a in actors:
        s_cap = a.max_serve_mw or max(a.flex_mw.max() * 4, 1e-6)
        bounds += ([(0, float(s_cap))] * T
                   + [(0, a.batt_mw)] * 2 * T)
    bounds += [(0, w) for _ in range(T) for w in widths]

    res = linprog(cost, A_ub=vstack(blocks), b_ub=np.concatenate(rhs),
                  A_eq=A_eq.tocsr(), b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"joint LP failed: {res.message}")
    out = []
    for ai, a in enumerate(actors):
        o = ai * per
        s = res.x[o:o + T]; c = res.x[o + T:o + 2 * T]; d = res.x[o + 2 * T:o + 3 * T]
        out.append(Dispatch(a.name, a.firm_mw + s + c - d, c - d))
    return out


@dataclass
class CoordinationResult:
    prices_base: np.ndarray
    net_base: np.ndarray
    selfish: dict = field(default_factory=dict)      # name -> Dispatch, plus totals
    rounds: list = field(default_factory=list)       # per-negotiation-round peaks
    coordinated: dict = field(default_factory=dict)


def run_scenario(actors, base_net, stack_x, stack_price, hour_adj,
                 n_rounds: int = 4, damping: float = 0.5):
    """Run all three regimes; return everything the story needs."""
    def price(net):
        return np.interp(net, stack_x, stack_price) + hour_adj

    p0 = price(base_net)
    # --- selfish: best-respond to base prices, then reprice the real total
    selfish_d = [price_taker_lp(a, p0) for a in actors]
    net_selfish = base_net + sum(d.draw_mw for d in selfish_d)
    # --- negotiation: damped best responses against the re-priced system
    # (damping in dispatch space keeps the constraint set — it's convex)
    rounds = []
    disp = selfish_d
    for k in range(n_rounds):
        net_k = base_net + sum(d.draw_mw for d in disp)
        rounds.append({"round": k + 1, "net": net_k, "peak_mw": float(net_k.max()),
                       "peak_price": float(price(net_k).max())})
        p_k = price(net_k)
        fresh = [price_taker_lp(a, p_k) for a in actors]
        disp = [Dispatch(o.actor,
                         (1 - damping) * o.draw_mw + damping * f.draw_mw,
                         (1 - damping) * o.battery_mw + damping * f.battery_mw)
                for o, f in zip(disp, fresh)]
    # --- coordinated bound
    joint_d = joint_lp(actors, base_net, stack_x, stack_price)
    net_joint = base_net + sum(d.draw_mw for d in joint_d)

    return {
        "p0": p0, "price_fn": price,
        "selfish": {"dispatches": selfish_d, "net": net_selfish},
        "rounds": rounds,
        "joint": {"dispatches": joint_d, "net": net_joint},
    }
