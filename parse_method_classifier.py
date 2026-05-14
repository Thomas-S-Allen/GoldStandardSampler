"""
gold_standard_sampler/parse_method_classifier.py
-------------------------------------------------
Determines the parse method (element_citation / mixed_citation / unstructured)
and source provenance from the format code and raw reference string.

Rules:
  XML format
    → element-citation if the raw string contains an <element-citation> tag
    → mixed-citation if it contains <mixed-citation> or any JATS/NLM tag
      with both structured content (tagged fields) AND free text nodes
    → unstructured as fallback (e.g. EGU references wrapped in XML but
      containing only a plain text string)

  TEXT format
    → unstructured, source_provenance = arxiv (most common TEXT source)
      The provenance is refined below using filename heuristics.

  OCR format
    → unstructured, source_provenance = ocr
"""

from __future__ import annotations

import re
from typing import Tuple
from xml.etree import ElementTree as ET

from models import ParseMethod, SourceProvenance

# Tags that indicate structured field content in JATS/NLM XML
_STRUCTURED_TAGS = frozenset([
    "element-citation", "mixed-citation",
    "surname", "given-names", "person-group",
    "year", "source", "volume", "fpage", "lpage",
    "article-title", "chapter-title", "pub-id",
    "publisher-name", "publisher-loc", "edition",
    "conf-name", "conf-date",
])

# Regex to detect any XML tag (used as a quick pre-check)
_ANY_TAG_RE = re.compile(r"<[a-zA-Z]")

# Known arXiv path fragments in result file names
_ARXIV_PATH_RE = re.compile(
    r"(astro-ph|cond\.?mat|hep-[a-z]+|gr-qc|nucl-[a-z]+|"
    r"quant-ph|math-ph|physics|nlin|cs\.|q-bio|q-fin|stat\.|"
    r"eess\.|econ\.|arXiv|arxiv|\d{4}\.\d{4,})",
    re.IGNORECASE,
)


def classify_parse_method(
    fmt: str,
    raw_string: str,
    matched_bibcode: str,
) -> Tuple[ParseMethod, SourceProvenance]:
    """
    Returns (ParseMethod, SourceProvenance) for a single reference.
    """
    fmt = fmt.upper()

    if fmt == "OCR":
        return ParseMethod.UNSTRUCTURED, SourceProvenance.OCR

    if fmt == "TEXT":
        return ParseMethod.UNSTRUCTURED, SourceProvenance.ARXIV

    # fmt == "XML"
    if not _ANY_TAG_RE.search(raw_string):
        # XML format file but this particular reference is a plain string
        # (e.g. EGU refplaintext fallback)
        return ParseMethod.UNSTRUCTURED, SourceProvenance.PUBLISHER_XML

    # Try to parse the XML fragment
    try:
        root = ET.fromstring(f"<root>{raw_string}</root>")
    except ET.ParseError:
        # Malformed XML — treat as unstructured
        return ParseMethod.UNSTRUCTURED, SourceProvenance.PUBLISHER_XML

    # Check for element-citation
    if root.find(".//element-citation") is not None:
        return ParseMethod.ELEMENT_CITATION, SourceProvenance.PUBLISHER_XML

    # Check for mixed-citation or any structured JATS tags
    has_structured_tags = any(
        root.find(f".//{tag}") is not None
        for tag in _STRUCTURED_TAGS
    )

    if not has_structured_tags:
        return ParseMethod.UNSTRUCTURED, SourceProvenance.PUBLISHER_XML

    # Has structured tags — determine if it is pure element-citation or mixed
    # A mixed-citation has both tagged children AND non-trivial text nodes
    has_free_text = _has_significant_free_text(root)
    if has_free_text:
        return ParseMethod.MIXED_CITATION, SourceProvenance.PUBLISHER_XML
    else:
        # All content is in tags — treat as element_citation even if the
        # root tag is <mixed-citation>
        return ParseMethod.ELEMENT_CITATION, SourceProvenance.PUBLISHER_XML


def _has_significant_free_text(root: ET.Element) -> bool:
    """
    Returns True if the XML element tree has non-trivial text nodes
    (i.e. text that is not just whitespace) outside of structured tags.
    This distinguishes a nominally mixed-citation that is fully tagged
    from one that genuinely has free-text islands.
    """
    for el in root.iter():
        # Check tail text (text after a closing tag, before next sibling)
        if el.tail and el.tail.strip():
            return True
        # Check direct text content of the root/mixed-citation element itself
        if el.tag in ("root", "mixed-citation") and el.text and el.text.strip():
            return True
    return False
