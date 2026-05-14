"""
gold_standard_sampler/ranking.py
---------------------------------
Computes a priority score for each GoldCandidate within its cell.
Higher score = higher priority for curator draw-down.

Scoring philosophy (from the governance section of the rationale document):
  - Mid-confidence matches are the most valuable calibration signal
    (~50% of calibration set target).
  - High-confidence matches are needed as a sanity baseline
    (~30% of calibration set target).
  - Low-confidence and no-match cases provide decision-boundary signal
    (~20% of calibration set target).

Within each slice, secondary ranking factors are:
  1. Underrepresented publication types score higher (thesis > proceedings >
     book > report > preprint > journal_article) to help meet absolute floors.
  2. References with more fields present score slightly higher within the
     structured strata (more fields → more informative for the scorer).
  3. References with fewer fields present score slightly higher within the
     unstructured stratum (harder cases → more calibration signal).
"""

from __future__ import annotations

from models import GoldCandidate, ParseMethod, SamplingSlice

# ── Sampling slice base scores ──────────────────────────────────────────────
_SLICE_BASE = {
    SamplingSlice.MID_CONFIDENCE:  0.60,
    SamplingSlice.HIGH_CONFIDENCE: 0.40,
    SamplingSlice.LOW_CONFIDENCE:  0.20,
    SamplingSlice.NO_MATCH:        0.10,
}

# ── Publication type bonus ──────────────────────────────────────────────────
# Rare types score higher to help meet absolute minimum floors.
_PUB_TYPE_BONUS = {
    "thesis":                 0.12,
    "conference_proceedings": 0.10,
    "book":                   0.08,
    "report":                 0.06,
    "preprint":               0.04,
    "software":               0.04,
    "dataset":                0.04,
    "journal_article":        0.00,
    "other":                  0.01,
    "unknown":                0.00,
}

# ── Publication type confidence bonus ──────────────────────────────────────
_PUB_TYPE_CONFIDENCE_BONUS = {
    "high":    0.03,
    "medium":  0.01,
    "low":     0.00,
    "unknown": 0.00,
}


def compute_priority_score(candidate: GoldCandidate) -> float:
    """
    Returns a float in [0, 1] representing sampling priority.
    Higher = draw this candidate first.
    """
    ref = candidate.reference

    # Base score from sampling slice
    score = _SLICE_BASE.get(ref.sampling_slice, 0.0)

    # Publication type bonus
    score += _PUB_TYPE_BONUS.get(ref.publication_type, 0.0)

    # Publication type confidence bonus
    score += _PUB_TYPE_CONFIDENCE_BONUS.get(ref.publication_type_confidence, 0.0)

    # Field presence adjustment
    n_fields = sum(1 for v in ref.field_presence.values() if v)
    total_fields = max(len(ref.field_presence), 1)
    field_ratio = n_fields / total_fields

    if ref.parse_method == ParseMethod.UNSTRUCTURED:
        # For unstructured: fewer fields present → harder case → higher priority
        # (but we still want some fields to be present for the scorer to work)
        # Sweet spot: 2–4 fields present out of 8
        if 2 <= n_fields <= 4:
            score += 0.05
        elif n_fields < 2:
            score += 0.02   # very sparse — useful but risky
    else:
        # For structured: more fields present → more informative
        score += 0.05 * field_ratio

    # Near-miss (status 5) gets a small bonus — valuable for boundary calibration
    if candidate.candidate.needs_adjudication:
        score += 0.04

    # Clip to [0, 1]
    return min(max(score, 0.0), 1.0)
