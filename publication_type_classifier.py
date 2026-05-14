"""
gold_standard_sampler/publication_type_classifier.py
-----------------------------------------------------
Infers publication type from a reference string using the cascade defined
in the gold-standard dataset rationale document:

  1. JATS publication-type attribute (XML, high confidence)
  2. Field presence pattern (medium confidence)
  3. String heuristics / known series name lookup (medium confidence)
  4. "journal_article" as default (low confidence if no other signal)
  5. "unknown" if genuinely unclassifiable (very short or garbled strings)

Returns (publication_type: str, confidence: str).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from xml.etree import ElementTree as ET

from config import PUBLICATION_TYPE_HEURISTICS
from models import ParseMethod

# ── JATS publication-type attribute values → normalised types ──────────────
_JATS_PUB_TYPE_MAP = {
    "journal":        "journal_article",
    "journal-article": "journal_article",
    "book":           "book",
    "book-chapter":   "book",
    "chapter":        "book",
    "conf-paper":     "conference_proceedings",
    "conf-proc":      "conference_proceedings",
    "confproc":       "conference_proceedings",
    "conference-paper": "conference_proceedings",
    "thesis":         "thesis",
    "dissertation":   "thesis",
    "report":         "report",
    "technical-report": "report",
    "preprint":       "preprint",
    "software":       "software",
    "dataset":        "dataset",
    "other":          "other",
}

# ── Compiled heuristic patterns from config ────────────────────────────────
_COMPILED_HEURISTICS = [
    (re.compile(pattern, re.IGNORECASE), pub_type)
    for pattern, pub_type in PUBLICATION_TYPE_HEURISTICS
]

# Minimum string length below which we declare the type unknown
_MIN_CLASSIFIABLE_LENGTH = 20


def classify_publication_type(
    parse_method: ParseMethod,
    raw_string: str,
    fmt: str,
) -> Tuple[str, str]:
    """
    Returns (publication_type, confidence).
    confidence is one of: "high", "medium", "low", "unknown".
    """
    if len(raw_string.strip()) < _MIN_CLASSIFIABLE_LENGTH:
        return "unknown", "unknown"

    # ── Step 1: JATS publication-type attribute ────────────────────────────
    if fmt.upper() == "XML":
        jats_type = _from_jats_attribute(raw_string)
        if jats_type is not None:
            return jats_type, "high"

    # ── Step 2: field presence pattern ────────────────────────────────────
    fp_type = _from_field_presence(raw_string, parse_method)
    if fp_type is not None:
        return fp_type, "medium"

    # ── Step 3: string heuristics / known series ──────────────────────────
    heuristic_type = _from_heuristics(raw_string)
    if heuristic_type is not None:
        return heuristic_type, "medium"

    # ── Step 4: default ───────────────────────────────────────────────────
    # If none of the above triggered, default to journal article
    # with low confidence (it is by far the most common type).
    return "journal_article", "low"


# ── Step 1 helpers ─────────────────────────────────────────────────────────

def _from_jats_attribute(raw_string: str) -> Optional[str]:
    """
    Parses the raw string as XML and looks for a publication-type attribute
    on the root citation element.
    """
    try:
        root = ET.fromstring(f"<root>{raw_string}</root>")
    except ET.ParseError:
        return None

    for tag in ("element-citation", "mixed-citation", "citation", "nlm-citation"):
        el = root.find(f".//{tag}")
        if el is not None:
            attr = el.get("publication-type", "").lower().strip()
            if attr in _JATS_PUB_TYPE_MAP:
                return _JATS_PUB_TYPE_MAP[attr]
            if attr:
                # Unknown JATS type — return as-is with "medium" confidence
                # (caller handles confidence; we return the raw value here)
                return attr

    return None


# ── Step 2 helpers ─────────────────────────────────────────────────────────

# Patterns for inferring type from field presence in unstructured strings
_YEAR_RE    = re.compile(r"\b(19|20)\d{2}\b")
_VOL_PAGE_RE = re.compile(r"\b\d+\s*[,(:]\s*\d+")   # vol, page-like pattern
_NO_VOL_PAGE_RE = re.compile(            # long title with no numbers → likely book/report
    r"^[^0-9]{60,}$"
)
_THESIS_FIELD_RE = re.compile(
    r"\b(?:Universit(?:y|ät|é|eit|ad)|Institut(?:e|o|ut)|"
    r"College|School|Fakultät|Facult[eé])\b",
    re.IGNORECASE,
)


def _from_field_presence(raw_string: str, parse_method: ParseMethod) -> Optional[str]:
    """
    Uses field presence patterns to infer publication type.
    Only applies to unstructured references; for structured references
    the JATS attribute is more reliable.
    """
    if parse_method != ParseMethod.UNSTRUCTURED:
        return None

    # No year at all → very unusual for a journal article
    if not _YEAR_RE.search(raw_string):
        return None  # let heuristics handle it

    # Has a DOI → most likely journal article or conference proceedings
    if re.search(r"10\.\d{4,}/", raw_string):
        return None  # not distinctive enough on its own

    # Institution-like tokens with no volume/page → likely thesis
    if _THESIS_FIELD_RE.search(raw_string) and not _VOL_PAGE_RE.search(raw_string):
        return "thesis"

    return None


# ── Step 3 helpers ─────────────────────────────────────────────────────────

def _from_heuristics(raw_string: str) -> Optional[str]:
    """
    Applies the compiled heuristic patterns from config.py in order.
    Returns the first matching publication type, or None.
    """
    for pattern, pub_type in _COMPILED_HEURISTICS:
        if pattern.search(raw_string):
            return pub_type
    return None
