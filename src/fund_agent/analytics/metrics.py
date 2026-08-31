from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import sqrt

import numpy as np
from pydantic import BaseModel

from fund_agent.domain.models import NavMetrics


class DrawdownMetrics(BaseModel):
    max_drawdown: float | None = None
    peak_index: int | None = None
    trough_index: int | None = None
    recovery_index: int | None = None


def _validated_values(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return array
    if not np.all(np.isfinite(array)):
        raise ValueError("values must be finite")
    if np.any(array <= 0):
        raise ValueError("NAV values must be positive")
    return array


def compute_drawdown(nav: Sequence[float]) -> DrawdownMetrics:
    values = _validated_values(nav)
    if values.size == 0:
        return DrawdownMetrics()

    running_peak = np.maximum.accumulate(values)
    drawdowns = values / running_peak - 1.0
    trough_index = int(np.argmin(drawdowns))
    peak_index = int(np.argmax(values[: trough_index + 1]))
    recovery_candidates = np.flatnonzero(values[trough_index + 1 :] >= values[peak_index])
    recovery_index = None
    if recovery_candidates.size:
        recovery_index = int(trough_index + 1 + recovery_candidates[0])

    return DrawdownMetrics(
        max_drawdown=float(drawdowns[trough_index]),
        peak_index=peak_index,
        trough_index=trough_index,
        recovery_index=recovery_index,
    )


def compute_nav_metrics(nav: Sequence[float], periods_per_year: int = 252) -> NavMetrics:
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    values = _validated_values(nav)
    if values.size == 0:
        return NavMetrics()

    total_return = float(values[-1] / values[0] - 1.0)
    drawdown = compute_drawdown(values.tolist())
    if values.size < 2:
        return NavMetrics(total_return=total_return, max_drawdown=drawdown.max_drawdown)

    returns = values[1:] / values[:-1] - 1.0
    volatility = float(np.std(returns, ddof=1) * sqrt(periods_per_year)) if returns.size > 1 else 0.0
    mean_return = float(np.mean(returns))
    sharpe = None if volatility == 0 else float(mean_return * periods_per_year / volatility)
    years = (values.size - 1) / periods_per_year
    growth_log = None if years <= 0 else float(np.log(values[-1] / values[0]) / years)
    annualized_return = None if growth_log is None or growth_log > 700 else float(np.expm1(growth_log))
    calmar = None
    if annualized_return is not None and drawdown.max_drawdown is not None and drawdown.max_drawdown < 0:
        calmar = float(annualized_return / abs(drawdown.max_drawdown))

    return NavMetrics(
        total_return=total_return,
        max_drawdown=drawdown.max_drawdown,
        volatility=volatility,
        sharpe=sharpe,
        calmar=calmar,
    )


def compute_portfolio_returns(
    weights: Mapping[str, float],
    returns: Mapping[str, Sequence[float]],
) -> list[float]:
    if not weights:
        return []
    missing = sorted(set(weights) - set(returns))
    if missing:
        raise ValueError(f"missing returns for: {', '.join(missing)}")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("weights must be non-negative")
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("weights must sum to a positive value")

    common_length = min(len(returns[code]) for code in weights)
    if common_length == 0:
        return []
    normalized = {code: weight / total_weight for code, weight in weights.items()}
    result: list[float] = []
    for index in range(common_length):
        result.append(sum(normalized[code] * float(returns[code][index]) for code in weights))
    return result
