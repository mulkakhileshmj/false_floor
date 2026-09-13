"""Exact small-sample statistics for the False Floor design.

The analysis unit is the matched twin pair, so n is 20, not 40 items and not
160 calls. Everything here is built for that.

The primary estimand is a difference in differences. For twin pair i, with x for
control and y for the cue:

    Delta_i = (x_safety_i - y_safety_i) - (x_benign_i - y_benign_i)

D_sel is the mean of Delta over pairs. An earlier version of this pack tested
that quantity with McNemar, which is wrong: McNemar tests marginal homogeneity
of a single paired comparison and cannot speak to a difference between two of
them. The correct test is a sign-flip permutation test, and at this sample size
it can be computed exactly rather than approximated.

The sign-flip procedure requires independent pairs and sign symmetry under the
null. Matching fields in a dataset does not prove those assumptions. Numerical
agreement with enumeration tests the implementation, not empirical calibration.
See PREREGISTRATION.md and REFERENCES.md for scope and background.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

Z95 = 1.959963984540054


# --------------------------------------------------------------------------
# exact sign-flip permutation test
# --------------------------------------------------------------------------

@dataclass
class SignFlipResult:
    """Outcome of an exact sign-flip test on per-pair differences."""

    mean: float
    p_value: float
    n_pairs: int
    n_informative: int
    exact: bool
    min_possible_p: float

    @property
    def underpowered(self) -> bool:
        """True when no result could have reached significance at 0.05.

        With k informative pairs the smallest attainable two-sided p is
        2 / 2**k, so k below 6 cannot produce a significant result whatever the
        effect size. Reporting this stops a null being read as evidence of
        absence.
        """
        return self.min_possible_p > 0.05


def _infer_denominator(values: list[float], max_denominator: int = 240) -> int | None:
    """Smallest d making every value an integer multiple of 1/d.

    Scores are means over repeats (epochs times option rotations), so they are
    rationals with a small common denominator. Finding it lets the null
    distribution be built by exact integer convolution instead of sampling.
    """
    for denominator in range(1, max_denominator + 1):
        if all(abs(v * denominator - round(v * denominator)) < 1e-9 for v in values):
            return denominator
    return None


def sign_flip_test(deltas: list[float]) -> SignFlipResult:
    """Exact two-sided sign-flip permutation test on per-pair differences.

    Builds the null distribution of the sum by convolution over the informative
    (non-zero) pairs, which is exact and costs microseconds. Pairs with a delta
    of exactly zero are excluded: flipping their sign changes nothing, so they
    scale the numerator and denominator identically.
    """
    n_pairs = len(deltas)
    if n_pairs == 0:
        return SignFlipResult(0.0, 1.0, 0, 0, True, 1.0)

    mean = sum(deltas) / n_pairs
    informative = [d for d in deltas if abs(d) > 1e-12]
    k = len(informative)
    if k == 0:
        return SignFlipResult(mean, 1.0, n_pairs, 0, True, 1.0)

    min_possible_p = min(1.0, 2.0 / (2**k))
    denominator = _infer_denominator(informative)

    if denominator is None:  # pragma: no cover - scores are always rational here
        return SignFlipResult(mean, _monte_carlo_p(informative), n_pairs, k, False,
                              min_possible_p)

    scaled = [int(round(d * denominator)) for d in informative]
    observed = abs(sum(scaled))

    # Null distribution of the sum under independent sign flips.
    counts: dict[int, int] = {0: 1}
    for value in scaled:
        nxt: dict[int, int] = defaultdict(int)
        for total, count in counts.items():
            nxt[total + value] += count
            nxt[total - value] += count
        counts = dict(nxt)

    total_assignments = 2**k
    at_least_as_extreme = sum(c for s, c in counts.items() if abs(s) >= observed)
    p_value = min(1.0, at_least_as_extreme / total_assignments)
    return SignFlipResult(mean, p_value, n_pairs, k, True, min_possible_p)


def _monte_carlo_p(values: list[float], iterations: int = 200_000) -> float:
    import random

    observed = abs(sum(values))
    rng = random.Random(0)
    hits = 0
    for _ in range(iterations):
        total = sum(v if rng.random() < 0.5 else -v for v in values)
        if abs(total) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (iterations + 1)


# --------------------------------------------------------------------------
# per-arm paired test
# --------------------------------------------------------------------------

def mcnemar_exact_p(down: int, up: int) -> float:
    """Exact two-sided conditional McNemar test."""
    n = down + up
    if n == 0:
        return 1.0
    k = min(down, up)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def mcnemar_midp(down: int, up: int) -> float:
    """Mid-p McNemar test, the variant recommended over the exact conditional.

    The exact conditional test is markedly conservative; across a large
    simulation study its actual type I error runs at about half nominal, so it
    throws away real power at exactly the sample sizes where power is scarce.
    The mid-p correction subtracts half the point probability of the observed
    outcome and is recommended in its place (Fagerland, Lydersen and Laake).

    Used here only as a per-arm secondary. The primary test on D_sel is the
    sign-flip test above.
    """
    n = down + up
    if n == 0:
        return 1.0
    k = min(down, up)
    point = math.comb(n, k) / (2**n)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * (tail - 0.5 * point))


# --------------------------------------------------------------------------
# intervals and corrections
# --------------------------------------------------------------------------

def wilson_interval(successes: int, total: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a single proportion.

    Preferred over Wald at small n, where Wald intervals are badly miscalibrated
    and can run outside [0, 1].
    """
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(deltas: list[float], iterations: int = 20_000,
                 seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean of per-pair differences.

    Plain percentile rather than BCa: at n=20 the bias-corrected accelerated
    interval has been found to undercover relative to nominal, and worse than
    simpler alternatives, so the extra machinery buys nothing here.
    """
    import random

    if not deltas:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(iterations):
        means.append(sum(deltas[int(rng.random() * n)] for _ in range(n)) / n)
    means.sort()
    lower = means[int(0.025 * iterations)]
    upper = means[min(iterations - 1, int(0.975 * iterations))]
    return (lower, upper)


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni step-down adjustment over a family of tests.

    Holm rather than Benjamini-Hochberg: this is a small confirmatory family
    where each cell is a claim about a named model, so controlling the
    family-wise error rate is the right target. Using a false-discovery-rate
    procedure on a dozen confirmatory tests reads as power shopping.
    """
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (key, p) in enumerate(ordered):
        value = min(1.0, (m - index) * p)
        running = max(running, value)  # enforce monotonicity
        adjusted[key] = running
    return adjusted


def minimum_detectable_effect(n_pairs: int, informative_rate: float = 0.35) -> float:
    """Rough smallest mean delta a sign-flip test could call significant.

    Deliberately crude. Its job is to keep the reader honest about what a null
    means at this sample size, not to substitute for the simulated power table
    in PREREGISTRATION.md.
    """
    k = max(1, int(round(n_pairs * informative_rate)))
    if 2.0 / (2**k) > 0.05:
        return float("inf")
    # All informative pairs moving the same way is the smallest configuration
    # that reaches the tail, so the effect must be at least that large.
    return k / n_pairs
