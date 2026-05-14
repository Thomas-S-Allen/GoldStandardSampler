"""
gold_standard_sampler/config.py
--------------------------------
All configurable constants for the sampling workflow.
"""

from __future__ import annotations

# ── Output ─────────────────────────────────────────────────────────────────
# Directory where sampler.py writes the JSONL candidate files.
OUTPUT_DIR = "output"

# Directory where report.py writes its report files.
# Kept separate from OUTPUT_DIR so reports do not clutter the JSONL directory.
REPORT_DIR = "reports"

# How many candidates to collect per cell before stopping.
# 50 × target_max gives plenty of choice for the curator draw-down
# while capping total output at ~500k records.
CANDIDATE_CAP_MULTIPLIER = 50

# Number of papers between progress log lines.
LOG_INTERVAL = 10_000

# Number of worker processes for parallel result file parsing.
# Set to 1 to disable multiprocessing (simplest, good for debugging).
# Set to os.cpu_count() or a fixed number for parallel operation.
WORKERS = 1

# ── Discipline codes as they appear in the input file ──────────────────────
DISCIPLINE_MAP = {
    "AST": "astrophysics",
    "PS":  "planetary_science",
    "HP":  "heliophysics",
    "GEO": "earth_science",
    "MUL": "multidisciplinary",
}

# ── Format codes as they appear in the input file ──────────────────────────
FORMAT_MAP = {
    "XML":  "xml",
    "TEXT": "text",
    "OCR":  "ocr",
}

# ── Match status values in result files ────────────────────────────────────
MATCH_STATUS = {
    "unmatched":  0,
    "matched":    1,
    "uncertain":  5,
}

# ── Stratification targets (min, max) pairs per (discipline, parse_method) ─
# parse_method values: "element_citation", "mixed_citation", "unstructured"
# These reflect the numbers from the gold-standard dataset rationale document
# (version after reviewer corrections):
#   element-citation totals: 2,700–3,200
#   mixed-citation totals:   1,250–1,700
#   unstructured totals:     3,000–3,900
#   negatives/near-misses:     500–1,000
TARGETS = {
    # Astrophysics
    ("astrophysics",      "element_citation"): (800,  900),
    ("astrophysics",      "mixed_citation"):   (300,  400),
    ("astrophysics",      "unstructured"):     (600,  800),
    # Earth Science
    ("earth_science",     "element_citation"): (700,  800),
    ("earth_science",     "mixed_citation"):   (400,  500),
    ("earth_science",     "unstructured"):     (700,  900),
    # Heliophysics
    ("heliophysics",      "element_citation"): (500,  600),
    ("heliophysics",      "mixed_citation"):   (200,  300),
    ("heliophysics",      "unstructured"):     (600,  800),
    # Planetary Science
    ("planetary_science", "element_citation"): (400,  500),
    ("planetary_science", "mixed_citation"):   (200,  300),
    ("planetary_science", "unstructured"):     (700,  900),
    # Multidisciplinary
    ("multidisciplinary", "element_citation"): (300,  400),
    ("multidisciplinary", "mixed_citation"):   (150,  200),
    ("multidisciplinary", "unstructured"):     (400,  500),
    # Negatives and near-misses (all disciplines combined)
    ("all",               "negatives"):        (500, 1000),
}

# ── Sampling slice score thresholds ────────────────────────────────────────
# These mirror the governance section of the gold-standard rationale document.
# ads_confidence >= HIGH  → high_confidence slice
# ads_confidence >= MID   → mid_confidence slice
# ads_confidence <  MID   → low_confidence slice
# unmatched               → no_match slice
SAMPLING_SLICE_THRESHOLDS = {
    "high": 0.85,
    "mid":  0.50,
}

# ── Publication type heuristics ────────────────────────────────────────────
# Patterns that strongly signal a specific publication type in unstructured
# reference strings, as a list of (pattern, publication_type) pairs.
# Evaluated in order; first match wins.
PUBLICATION_TYPE_HEURISTICS = [
    # Theses
    (r"\b(?:PhD|Ph\.D|doctoral|thesis|dissertation|Thesis|Dissertation)\b",
     "thesis"),
    # Conference proceedings — common conference series abbreviations
    (r"\b(?:Proc(?:eedings)?\.?\s+of|LPSC|LPI\s+Contrib|"
     r"AGU\s+(?:Fall|Spring)\s+Meeting|IAU\s+Symp|"
     r"SPIE\s+Proc|Conf\.\s+Proc|AIPC|AIP\s+Conf|"
     r"ASP\s+Conf|ASPC|BAAS|EGU|DPS\s+Meeting|"
     r"Ann\.\s+Geophys|Abstract|Abstracts)\b",
     "conference_proceedings"),
    # Books / book chapters
    (r"\b(?:eds?\.|editors?|edited\s+by|(?:New\s+York|London|Berlin|"
     r"Cambridge|Oxford|Amsterdam|Heidelberg)\s*[:,]|"
     r"Springer|Wiley|Elsevier|Academic\s+Press|"
     r"Cambridge\s+University\s+Press|Oxford\s+University\s+Press|"
     r"MIT\s+Press|Princeton\s+University\s+Press)\b",
     "book"),
    # Technical reports
    (r"\b(?:Tech(?:nical)?\s+Rep(?:ort)?|USGS|NASA\s+Tech|"
     r"Jet\s+Propulsion\s+Lab|ESA\s+Tech|"
     r"NCAR(?:/TN)?|NOAA|Atmos\.\s+Sci\.|"
     r"Open-?File\s+Rep|Scientific\s+Investigations\s+Report)\b",
     "report"),
    # Preprints
    (r"\b(?:arXiv|e-?print|preprint|submitted\s+to|in\s+prep(?:aration)?)\b",
     "preprint"),
]

# ── Within-cell publication type minimum floors ────────────────────────────
# Absolute minimum number of pairs per discipline (summed across parse methods)
# for each non-journal publication type.
# These are checked in the summary report but not enforced during sampling
# (enforcement is the curator's responsibility during draw-down).
PUB_TYPE_ABSOLUTE_MINIMUMS = {
    "conference_proceedings": 20,
    "thesis":                 25,
    "book":                   20,
    "report":                 10,
}
