"""Level 1: residential energy system simulation (paper Section 7.1, Table 1).

State  S_t = (R_t, P_t^pv, L_t, p_t^buy, p_t^sell)
Action x_t in [-D_max, C_max]  (positive = charge, negative = discharge, kW)
Battery dynamics with per-leg efficiency eta = sqrt(round-trip):
    R_{t+1} = R_t + eta * max(x,0) * dt - (1/eta) * max(-x,0) * dt
Cost   C_t = [max(g,0) * p_buy - max(-g,0) * p_sell] * dt,
       g_t = L_t - P_t^pv + x_t   (net grid import power)
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SystemParams:
    """Technical and economic parameters (paper Table 1)."""

    battery_capacity_kwh: float = 10.0
    round_trip_efficiency: float = 0.90
    initial_soc: float = 0.50            # fraction of capacity
    max_charge_kw: float = 10.0          # C_max (prompt: power_charge <= 10)
    max_discharge_kw: float = 5.0        # D_max (prompt: power_discharge >= -5)

    base_load_kw: float = 0.25
    morning_peak_kw: float = 1.25        # total demand at morning peak (07:30)
    evening_peak_kw: float = 2.25        # total demand at evening peak (19:00)
    morning_peak_hour: float = 7.5
    evening_peak_hour: float = 19.0
    morning_peak_width_h: float = 0.75   # Gaussian sigma
    evening_peak_width_h: float = 1.05
    load_noise_kw: float = 0.03

    pv_nominal_kwp: float = 5.0
    pv_sunrise_h: float = 6.0
    pv_sunset_h: float = 18.0
    pv_noise_rel: float = 0.22           # multiplicative noise sigma
    pv_irradiance_mean: float = 0.68     # mean of the multiplicative factor

    grid_buy_price: float = 0.35         # mean [eur/kWh]
    feed_in_tariff: float = 0.08         # fixed sell price [eur/kWh]
    price_peak_amplitude: float = 0.02   # sinusoidal morning/evening peaks
    price_noise: float = 0.031           # additive noise sigma -> ~10 % volatility
    price_noise_phi: float = 0.0         # AR(1) correlation of the price noise

    days: int = 7
    dt_h: float = 1.0                    # 60 min
    seed: int = 42

    @property
    def horizon(self) -> int:
        return int(self.days * 24 / self.dt_h)

    @property
    def eta_leg(self) -> float:
        """Per-leg efficiency so that the round trip matches Table 1."""
        return float(np.sqrt(self.round_trip_efficiency))


@dataclass
class ExogenousSeries:
    """Fixed-seed realization of W_t (paper Section 7.1.3)."""

    pv_kw: np.ndarray
    load_kw: np.ndarray
    buy_price: np.ndarray
    sell_price: np.ndarray

    @property
    def price_volatility_pct(self) -> float:
        return float(np.std(self.buy_price) / np.mean(self.buy_price) * 100.0)


def generate_exogenous(params: SystemParams) -> ExogenousSeries:
    """PV: sinusoidal 06:00-18:00 profile with multiplicative noise, clipped at
    nominal power. Load: base + Gaussian morning/evening peaks + additive noise.
    Buy price: mean with sinusoidal morning/evening peaks + random volatility;
    feed-in tariff constant."""
    rng = np.random.default_rng(params.seed)
    t = np.arange(params.horizon) * params.dt_h
    h = t % 24.0

    # PV generation
    daylight = (h >= params.pv_sunrise_h) & (h <= params.pv_sunset_h)
    span = params.pv_sunset_h - params.pv_sunrise_h
    pv_clear = np.where(
        daylight,
        params.pv_nominal_kwp * np.sin(np.pi * (h - params.pv_sunrise_h) / span),
        0.0,
    )
    irradiance = rng.normal(params.pv_irradiance_mean, params.pv_noise_rel, params.horizon)
    pv = pv_clear * np.clip(irradiance, 0.0, None)
    pv = np.clip(pv, 0.0, params.pv_nominal_kwp)

    # Household load
    morning = (params.morning_peak_kw - params.base_load_kw) * np.exp(
        -0.5 * ((h - params.morning_peak_hour) / params.morning_peak_width_h) ** 2
    )
    evening = (params.evening_peak_kw - params.base_load_kw) * np.exp(
        -0.5 * ((h - params.evening_peak_hour) / params.evening_peak_width_h) ** 2
    )
    load = params.base_load_kw + morning + evening
    load = load + rng.normal(0.0, params.load_noise_kw, params.horizon)
    load = np.clip(load, 0.05, None)

    # Market prices: sinusoid peaking at ~07:30 and ~19:30 plus noise
    peaks = np.sin(2.0 * np.pi * (h - 4.5) / 12.0)
    phi = params.price_noise_phi
    innov = rng.normal(0.0, params.price_noise * np.sqrt(1.0 - phi**2), params.horizon)
    noise = np.empty(params.horizon)
    noise[0] = rng.normal(0.0, params.price_noise)
    for t in range(1, params.horizon):
        noise[t] = phi * noise[t - 1] + innov[t]
    buy = params.grid_buy_price + params.price_peak_amplitude * peaks + noise
    buy = np.clip(buy, 0.05, None)
    sell = np.full(params.horizon, params.feed_in_tariff)

    return ExogenousSeries(pv_kw=pv, load_kw=load, buy_price=buy, sell_price=sell)


@dataclass
class SimulationResult:
    total_cost: float
    cost_per_step: np.ndarray
    soc_kwh: np.ndarray          # length T+1, includes initial state
    actions_kw: np.ndarray       # applied (clipped) actions
    requested_kw: np.ndarray     # raw policy outputs
    utilization_pct: float = 0.0
    avg_soc_pct: float = 0.0
    runtime_s: float = 0.0
    extra: dict = field(default_factory=dict)


def clip_action(x: float, soc: float, params: SystemParams) -> float:
    """Enforce admissible power limits and SoC bounds (simulation clips
    infeasible policy outputs, paper Section 7.1.2)."""
    x = float(np.clip(x, -params.max_discharge_kw, params.max_charge_kw))
    eta = params.eta_leg
    if x > 0:  # cannot charge above capacity
        max_charge = (params.battery_capacity_kwh - soc) / (eta * params.dt_h)
        x = min(x, max_charge)
    else:      # cannot discharge below empty
        max_discharge = soc * eta / params.dt_h
        x = max(x, -max_discharge)
    return x


def simulate(policy, series: ExogenousSeries, params: SystemParams) -> SimulationResult:
    """Run the policy over the full horizon. `policy` exposes
    take_action(current_energy_stored_kwh, current_pv_generation_kw,
    current_demand_kw, current_grid_buy_price, current_grid_sell_price,
    battery_capacity_kwh) -> float (kW; positive charge, negative discharge)."""
    T = params.horizon
    soc = np.empty(T + 1)
    soc[0] = params.initial_soc * params.battery_capacity_kwh
    actions = np.empty(T)
    requested = np.empty(T)
    costs = np.empty(T)
    eta = params.eta_leg

    for t in range(T):
        raw = policy.take_action(
            current_energy_stored_kwh=float(soc[t]),
            current_pv_generation_kw=float(series.pv_kw[t]),
            current_demand_kw=float(series.load_kw[t]),
            current_grid_buy_price=float(series.buy_price[t]),
            current_grid_sell_price=float(series.sell_price[t]),
            battery_capacity_kwh=params.battery_capacity_kwh,
        )
        raw = float(raw)
        if not np.isfinite(raw):
            raise ValueError(f"take_action returned non-finite value {raw!r} at t={t}")
        requested[t] = raw
        x = clip_action(raw, soc[t], params)
        actions[t] = x
        soc[t + 1] = soc[t] + eta * max(x, 0.0) * params.dt_h - (1.0 / eta) * max(-x, 0.0) * params.dt_h
        g = series.load_kw[t] - series.pv_kw[t] + x
        costs[t] = (
            max(g, 0.0) * series.buy_price[t] - max(-g, 0.0) * series.sell_price[t]
        ) * params.dt_h

    active = np.abs(actions) > 1e-3
    return SimulationResult(
        total_cost=float(costs.sum()),
        cost_per_step=costs,
        soc_kwh=soc,
        actions_kw=actions,
        requested_kw=requested,
        utilization_pct=float(active.mean() * 100.0),
        avg_soc_pct=float(soc.mean() / params.battery_capacity_kwh * 100.0),
    )


class NoBatteryPolicy:
    def take_action(self, **kwargs) -> float:
        return 0.0
