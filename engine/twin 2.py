"""Digital twin: a calibrated merit-order supply stack.

The twin reconstructs the price-setting supply curve as a monotone function
price = stack(net_load) fitted to observed day-ahead LMPs (isotonic
regression = nonparametric merit order), with an hour-of-day congestion/scarcity
adjustment. Calibration quality is measured out-of-sample and is the
credibility anchor: once simulated prices track observed LMPs, the same stack
prices counterfactual load injections.

The integral of the monotone stack is a convex piecewise-linear system cost
function, which the optimizer exploits directly (LP-representable).
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


@dataclass
class CalibrationReport:
    mae: float
    rmse: float
    mape: float
    corr: float
    peak_mae: float          # MAE over top-decile price hours
    n_train: int
    n_test: int


@dataclass
class MeritOrderTwin:
    stack: IsotonicRegression = None
    hour_adj: pd.Series = None          # additive hour-of-day adjustment [$/MWh]
    grid: np.ndarray = None             # net-load grid for the stack curve
    grid_price: np.ndarray = None
    report: CalibrationReport = None
    _train_frame: pd.DataFrame = field(default=None, repr=False)

    # ---------------- calibration ----------------

    @classmethod
    def calibrate(cls, df: pd.DataFrame, test_frac: float = 0.2) -> "MeritOrderTwin":
        """Fit stack + hourly adjustment on the first (1-test_frac) of the
        period; report accuracy on the held-out tail."""
        df = df.sort_values("time").reset_index(drop=True)
        n_test = int(len(df) * test_frac)
        train, test = df.iloc[:-n_test], df.iloc[-n_test:]

        twin = cls()
        twin.stack = IsotonicRegression(out_of_bounds="clip", increasing=True)
        twin.stack.fit(train["net_load_mw"], train["LMP"])

        resid = train["LMP"] - twin.stack.predict(train["net_load_mw"])
        twin.hour_adj = resid.groupby(train["time"].dt.hour).mean()

        lo, hi = df["net_load_mw"].min(), df["net_load_mw"].max()
        twin.grid = np.linspace(lo * 0.7, hi * 1.35, 400)
        twin.grid_price = twin.stack.predict(twin.grid)
        # extend the stack beyond observed net load: scarcity slope from the
        # steepest observed decile so injected load climbs the stack
        top = twin.grid > hi
        if top.any():
            steep = np.percentile(np.diff(twin.grid_price[~top]) /
                                  np.diff(twin.grid[~top]), 90)
            steep = max(steep, 1e-4)
            base = twin.grid_price[~top][-1]
            twin.grid_price[top] = base + steep * (twin.grid[top] - hi) * 3.0
        twin._train_frame = train

        pred = twin.predict(test["net_load_mw"].values, test["time"])
        err = test["LMP"].values - pred
        peak = test["LMP"] >= test["LMP"].quantile(0.9)
        twin.report = CalibrationReport(
            mae=float(np.abs(err).mean()),
            rmse=float(np.sqrt((err ** 2).mean())),
            mape=float(np.abs(err / test["LMP"].values).mean() * 100),
            corr=float(np.corrcoef(pred, test["LMP"].values)[0, 1]),
            peak_mae=float(np.abs(err[peak.values]).mean()),
            n_train=len(train), n_test=len(test),
        )
        return twin

    # ---------------- pricing ----------------

    def price_curve(self, net_load: np.ndarray) -> np.ndarray:
        """Stack price for arbitrary net load (with scarcity extension)."""
        return np.interp(net_load, self.grid, self.grid_price)

    def predict(self, net_load: np.ndarray, times: pd.Series) -> np.ndarray:
        adj = pd.Series(times.dt.hour.values).map(self.hour_adj).fillna(0.0).values
        return self.price_curve(np.asarray(net_load, dtype=float)) + adj

    def system_cost_segments(self, n_seg: int = 40):
        """Piecewise-linear convex system cost: breakpoints of the stack for
        LP formulations (marginal cost = stack price, nondecreasing)."""
        idx = np.linspace(0, len(self.grid) - 1, n_seg).astype(int)
        return self.grid[idx], self.grid_price[idx]
