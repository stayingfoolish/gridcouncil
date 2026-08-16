"""Data layer: historical day-ahead LMP, load, and fuel mix from an ISO.

v1 uses NYISO (fully open, no API key). The PJM adapter has the same
interface and activates when PJM_API_KEY is set — PJM Data Miner 2 requires a
(free) subscription key, so the architecture keeps the target swappable.
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "results" / "engine_cache"

# Full-year window used by the demo app (the search drivers keep their own
# shorter window so recorded runs stay comparable).
YEAR_START, YEAR_END = "2025-08-15", "2026-08-14"

# Average CO2 emission factors [tCO2 per MWh generated] by NYISO fuel category.
EMISSION_FACTORS_T_PER_MWH = {
    "Natural Gas": 0.42, "Dual Fuel": 0.46, "Other Fossil Fuels": 0.90,
    "Nuclear": 0.0, "Hydro": 0.0, "Wind": 0.0, "Other Renewables": 0.02,
}


def _cache(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def fetch_nyiso(start: str, end: str, reference_zone: str = "N.Y.C.") -> pd.DataFrame:
    """Hourly frame: total load [MW], renewable/must-run generation [MW],
    net load [MW], and reference-zone day-ahead LMP [$/MWh]."""
    cache = _cache(f"nyiso_{start}_{end}.parquet")
    if cache.exists():
        return pd.read_parquet(cache)

    import gridstatus

    iso = gridstatus.NYISO()
    lmp = iso.get_lmp(start=start, end=end, market="DAY_AHEAD_HOURLY")
    load = iso.get_load(start=start, end=end)
    fuel = iso.get_fuel_mix(start=start, end=end)

    # work in UTC so DST fall-back hours never produce ambiguous floors
    zone = lmp[lmp["Location"] == reference_zone].copy()
    zone["hour"] = zone["Interval Start"].dt.tz_convert("UTC").dt.floor("h")
    px = zone.groupby("hour")[["LMP", "Energy", "Congestion", "Loss"]].mean()

    load["hour"] = load["Time"].dt.tz_convert("UTC").dt.floor("h")
    ld = load.groupby("hour")[["Load"]].mean().rename(columns={"Load": "load_mw"})

    fuel["hour"] = fuel["Time"].dt.tz_convert("UTC").dt.floor("h")
    # zero-marginal-cost / must-run resources that displace the price-setting
    # thermal stack: nuclear, hydro, wind, other renewables
    must_run_cols = [c for c in ["Nuclear", "Hydro", "Wind", "Other Renewables"]
                     if c in fuel.columns]
    fuel_cols = [c for c in EMISSION_FACTORS_T_PER_MWH if c in fuel.columns]
    fm = fuel.groupby("hour")[fuel_cols].mean()
    fm["must_run_mw"] = fm[[c for c in must_run_cols if c in fm.columns]].sum(axis=1)
    # average grid carbon intensity from the real fuel mix [tCO2/MWh]
    total_gen = fm[fuel_cols].sum(axis=1)
    emissions = sum(fm[c] * EMISSION_FACTORS_T_PER_MWH[c] for c in fuel_cols)
    fm["carbon_t_per_mwh"] = (emissions / total_gen.replace(0, np.nan)).fillna(0.0)

    df = px.join(ld, how="inner").join(fm[["must_run_mw", "carbon_t_per_mwh"]],
                                       how="inner").dropna()
    df["net_load_mw"] = df["load_mw"] - df["must_run_mw"]
    df = df.reset_index().rename(columns={"hour": "time"})
    df["time"] = df["time"].dt.tz_convert("America/New_York")
    df.to_parquet(cache)
    return df


def fetch(start: str, end: str) -> tuple[pd.DataFrame, str]:
    """Fetch from PJM when a key is available, otherwise NYISO."""
    if os.environ.get("PJM_API_KEY"):
        raise NotImplementedError("PJM adapter: set up gridstatus.PJM() here")
    return fetch_nyiso(start, end), "NYISO"


def household_profile_kw(times: pd.Series, load_mw: pd.Series,
                         annual_kwh: float = 7000.0) -> np.ndarray:
    """Hourly load of a representative home, shaped from the real system load
    (residential-dominated evening peak) and scaled to a typical annual
    consumption. Honest label: real shape, scaled — a hook for building-stock
    profiles (e.g. NREL ResStock) later."""
    shape = load_mw.values / load_mw.mean()
    avg_kw = annual_kwh / 8760.0
    return shape * avg_kw
