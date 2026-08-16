"""Data layer: historical day-ahead LMP, load, and fuel mix from an ISO.

v1 uses NYISO (fully open, no API key). The PJM adapter has the same
interface and activates when PJM_API_KEY is set — PJM Data Miner 2 requires a
(free) subscription key, so the architecture keeps the target swappable.
"""

import os
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "results" / "engine_cache"


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

    zone = lmp[lmp["Location"] == reference_zone].copy()
    zone["hour"] = zone["Interval Start"].dt.floor("h")
    px = zone.groupby("hour")[["LMP", "Energy", "Congestion", "Loss"]].mean()

    load["hour"] = load["Time"].dt.floor("h")
    ld = load.groupby("hour")[["Load"]].mean().rename(columns={"Load": "load_mw"})

    fuel["hour"] = fuel["Time"].dt.floor("h")
    # zero-marginal-cost / must-run resources that displace the price-setting
    # thermal stack: nuclear, hydro, wind, other renewables
    must_run_cols = [c for c in ["Nuclear", "Hydro", "Wind", "Other Renewables"]
                     if c in fuel.columns]
    fm = fuel.groupby("hour")[must_run_cols].mean()
    fm["must_run_mw"] = fm.sum(axis=1)

    df = px.join(ld, how="inner").join(fm[["must_run_mw"]], how="inner").dropna()
    df["net_load_mw"] = df["load_mw"] - df["must_run_mw"]
    df = df.reset_index().rename(columns={"hour": "time"})
    df.to_parquet(cache)
    return df


def fetch(start: str, end: str) -> tuple[pd.DataFrame, str]:
    """Fetch from PJM when a key is available, otherwise NYISO."""
    if os.environ.get("PJM_API_KEY"):
        raise NotImplementedError("PJM adapter: set up gridstatus.PJM() here")
    return fetch_nyiso(start, end), "NYISO"
