"""Scenario planner: inject a new load into the twin and price the
counterfactual — the 'how much load can the grid welcome' half of the engine."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class NewLoad:
    name: str
    profile_mw: np.ndarray          # hourly MW draw (before flexibility)
    deferrable_frac: float = 0.0    # share of energy that can shift in time
    defer_window_h: int = 24        # max deferral
    battery_mwh: float = 0.0
    battery_mw: float = 0.0
    battery_rt_eff: float = 0.88


def datacenter(hours: int, mw: float = 500.0, deferrable_frac: float = 0.5,
               battery_mwh: float = 400.0, battery_mw: float = 100.0) -> NewLoad:
    """Flat-baseload data center with deferrable compute and on-site storage."""
    return NewLoad("data center", np.full(hours, mw), deferrable_frac, 24,
                   battery_mwh, battery_mw)


def ev_ramp(times: pd.Series, fleet_mw_peak: float = 300.0) -> NewLoad:
    """Evening-weighted EV charging ramp (arrive ~18:00, plugged until 07:00)."""
    h = times.dt.hour.values
    shape = np.where((h >= 17) | (h <= 1), 1.0, np.where((h >= 2) & (h <= 6), 0.35, 0.1))
    return NewLoad("EV fleet", fleet_mw_peak * shape, deferrable_frac=0.8,
                   defer_window_h=12)


@dataclass
class ScenarioImpact:
    price_base: np.ndarray
    price_new: np.ndarray
    avg_price_delta: float          # $/MWh, load-weighted for existing consumers
    peak_price_delta: float
    consumer_bill_delta: float      # $ over the horizon, existing load only
    newload_energy_cost: float      # $ the new load itself pays


def assess(twin, frame: pd.DataFrame, load: NewLoad,
           injected_mw: np.ndarray | None = None) -> ScenarioImpact:
    """Price the system with and without the injected profile."""
    inj = load.profile_mw if injected_mw is None else injected_mw
    base_net = frame["net_load_mw"].values
    p0 = twin.predict(base_net, frame["time"])
    p1 = twin.predict(base_net + inj, frame["time"])
    existing = frame["load_mw"].values
    w = existing / existing.sum()
    return ScenarioImpact(
        price_base=p0, price_new=p1,
        avg_price_delta=float(((p1 - p0) * w).sum()),
        peak_price_delta=float((p1 - p0).max()),
        consumer_bill_delta=float(((p1 - p0) * existing).sum()),
        newload_energy_cost=float((p1 * inj).sum()),
    )
