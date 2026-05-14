"""
gold_standard_sampler/models.py
--------------------------------
Data classes representing the core entities produced by the sampling workflow.
All classes are Python 3.8 compatible (no structural pattern matching,
no walrus operator in class bodies, dataclasses used throughout).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ParseMethod(str, Enum):
    ELEMENT_CITATION = "element_citation"
    MIXED_CITATION   = "mixed_citation"
    UNSTRUCTURED     = "unstructured"


class SourceProvenance(str, Enum):
    PUBLISHER_XML = "publisher_xml"
    ARXIV         = "arxiv"
    OCR           = "ocr"
    OTHER         = "other"


class SamplingSlice(str, Enum):
    HIGH_CONFIDENCE = "high_confidence"
    MID_CONFIDENCE  = "mid_confidence"
    LOW_CONFIDENCE  = "low_confidence"
    NO_MATCH        = "no_match"


@dataclass
class ReferenceInstance:
    """
    Corresponds to the reference_instance table in the gold-standard schema.
    """
    id:                         str
    reference_string:           str
    source_document_id:         str            # bibcode of citing paper
    source_filename:            str
    parse_method:               ParseMethod
    source_provenance:          SourceProvenance
    discipline:                 str            # normalised discipline name
    publication_type:           str            # inferred publication type
    publication_type_confidence: str           # "high", "medium", "low", "unknown"
    field_presence:             Dict[str, bool]
    ads_confidence_score:       float          # synthesised per-reference confidence
    sampling_slice:             SamplingSlice
    # Article-level counts from the input file (for context)
    article_n_total:            int
    article_n_matched:          int
    article_n_uncertain:        int
    article_n_unmatched:        int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":                          self.id,
            "reference_string":            self.reference_string,
            "source_document_id":          self.source_document_id,
            "source_filename":             self.source_filename,
            "parse_method":                self.parse_method.value,
            "source_provenance":           self.source_provenance.value,
            "discipline":                  self.discipline,
            "publication_type":            self.publication_type,
            "publication_type_confidence": self.publication_type_confidence,
            "field_presence":              self.field_presence,
            "ads_confidence_score":        self.ads_confidence_score,
            "sampling_slice":              self.sampling_slice.value,
            "article_n_total":             self.article_n_total,
            "article_n_matched":           self.article_n_matched,
            "article_n_uncertain":         self.article_n_uncertain,
            "article_n_unmatched":         self.article_n_unmatched,
        }


@dataclass
class CandidateRecord:
    """
    Corresponds to the candidate_record table in the gold-standard schema.
    scix_id is left None here; it is resolved during database loading.
    """
    id:                 str
    bibcode:            Optional[str]   # matched bibcode or None
    scix_id:            Optional[str]   # resolved during DB load
    negative_type:      Optional[str]   # None | "near_miss" | "true_no_match"
    needs_adjudication: bool            # True for status-5 near-misses

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":                 self.id,
            "bibcode":            self.bibcode,
            "scix_id":            self.scix_id,
            "negative_type":      self.negative_type,
            "needs_adjudication": self.needs_adjudication,
        }


@dataclass
class GoldCandidate:
    """
    A fully annotated (reference, candidate) pair ready for curator review.
    Corresponds to one row in a cell JSONL file.
    """
    reference:    ReferenceInstance
    candidate:    CandidateRecord
    match_status: int   # 0, 1, or 5 — the raw ADS resolver status
    # priority_score is set by ranking.compute_priority_score() before writing
    priority_score: float = field(default=0.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference":      self.reference.to_dict(),
            "candidate":      self.candidate.to_dict(),
            "match_status":   self.match_status,
            "priority_score": round(self.priority_score, 4),
        }
