from __future__ import annotations

from decimal import Decimal
from math import sqrt
from statistics import mean, median, pstdev


def percentile(values: list[Decimal], percentile_value: Decimal) -> Decimal:
    if not values:
        raise ValueError("values cannot be empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def median_absolute_deviation(values: list[Decimal]) -> Decimal:
    center = Decimal(str(median(values)))
    return Decimal(str(median([abs(value - center) for value in values])))


def outlier_indices(
    values: list[Decimal],
    *,
    mad_z_threshold: Decimal,
    iqr_multiplier: Decimal,
    minimum_sample: int,
) -> set[int]:
    if len(values) < minimum_sample:
        return set()
    center = Decimal(str(median(values)))
    mad = median_absolute_deviation(values)
    if mad > 0:
        scale = Decimal("0.6745")
        return {
            index
            for index, value in enumerate(values)
            if scale * abs(value - center) / mad > mad_z_threshold
        }
    q1 = percentile(values, Decimal("0.25"))
    q3 = percentile(values, Decimal("0.75"))
    iqr = q3 - q1
    if iqr == 0:
        return {index for index, value in enumerate(values) if value != center}
    lower = q1 - iqr_multiplier * iqr
    upper = q3 + iqr_multiplier * iqr
    return {index for index, value in enumerate(values) if value < lower or value > upper}


def descriptive_statistics(values: list[Decimal]) -> dict[str, Decimal | int]:
    if not values:
        raise ValueError("values cannot be empty")
    floats = [float(value) for value in values]
    return {
        "count": len(values),
        "mean": Decimal(str(mean(floats))),
        "median": Decimal(str(median(floats))),
        "p10": percentile(values, Decimal("0.10")),
        "p25": percentile(values, Decimal("0.25")),
        "p50": percentile(values, Decimal("0.50")),
        "p75": percentile(values, Decimal("0.75")),
        "p90": percentile(values, Decimal("0.90")),
        "standard_deviation": Decimal(str(pstdev(floats))) if len(values) > 1 else Decimal("0"),
        "mad": median_absolute_deviation(values),
    }


def error_percent(actual: Decimal, estimated: Decimal) -> Decimal:
    if actual == 0:
        raise ValueError("actual quantity cannot be zero")
    return (actual - estimated) / actual * Decimal("100")


def error_dashboard(errors: list[Decimal]) -> dict[str, Decimal]:
    if not errors:
        return {}
    absolute = [abs(value) for value in errors]
    return {
        "mean_absolute_percentage_error": Decimal(str(mean(map(float, absolute)))),
        "median_absolute_percentage_error": Decimal(str(median(map(float, absolute)))),
        "bias": Decimal(str(mean(map(float, errors)))),
        "p50_error": percentile(absolute, Decimal("0.50")),
        "p90_error": percentile(absolute, Decimal("0.90")),
        "root_mean_square_error": Decimal(
            str(sqrt(mean(float(value * value) for value in errors)))
        ),
    }
