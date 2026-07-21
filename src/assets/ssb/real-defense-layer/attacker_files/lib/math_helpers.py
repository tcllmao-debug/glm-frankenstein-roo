"""
lib/math_helpers.py — Math utility functions.
"""
import math
from typing import Iterable

def mean(values: Iterable[float]) -> float:
    """Compute the arithmetic mean."""
    vs = list(values)
    if not vs:
        return 0.0
    return sum(vs) / len(vs)

def stddev(values: Iterable[float]) -> float:
    """Compute the population standard deviation."""
    vs = list(values)
    if len(vs) < 2:
        return 0.0
    m = mean(vs)
    var = sum((v - m) ** 2 for v in vs) / len(vs)
    return math.sqrt(var)

def clamp(x: float, lo: float, hi: float) -> float:
    """Clamp x to the range [lo, hi]."""
    return max(lo, min(hi, x))

def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between a and b by t in [0, 1]."""
    return a + (b - a) * clamp(t, 0.0, 1.0)
