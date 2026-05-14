"""
gold_standard_sampler/sampler.py  (v3 — multi-file interleaved)
------------------------------------------------------------------
Accepts one or more input files and interleaves rows across them so that
every format (XML, TEXT, OCR) contributes to cells simultaneously.
Early stopping halts once all cells reach their candidate cap.

Usage:
  python sampler.py xml_shuffled.txt text_shuffled.txt ocr_shuffled.txt

Python 3.8 compatible.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Tuple
from xml.etree import ElementTree as ET

from config import (
    CANDIDATE_CAP_MULTIPLIER,
    DISCIPLINE_MAP,
    LOG_INTERVAL,
    MATCH_STATUS,
    OUTPUT_DIR,
    TARGETS,
)
from models import (
    CandidateRecord,
    GoldCandidate,
    ParseMethod,
    ReferenceInstance,
    SamplingSlice,
    SourceProvenance,
)
from parse_method_classifier import classify_parse_method
from publication_type_classifier import classify_publication_type
from ranking import compute_priority_score

logger = logging.getLogger(__name__)


def _build_caps() -> Dict[str, int]:
    caps: Dict[str, int] = {}
    for (disc, pm), (_, tmax) in TARGETS.items():
        if pm == "negatives":
            continue
        caps[f"{disc}__{pm}"] = tmax * CANDIDATE_CAP_MULTIPLIER
    return caps


def run(*input_files: str) -> Dict[str, int]:
    """Main workflow. Accepts one or more input files. Returns {cell_key: candidates_written}.
    Input files are automatically sorted by descending line count so the
    largest file leads each interleaving round.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Sort inputs largest-first so format-specific cells (e.g. element_citation
    # from XML) start filling immediately in round 1 of the interleaver.
    if len(input_files) > 1:
        input_files = tuple(_sort_inputs_by_size(list(input_files)))

    caps = _build_caps()
    cell_counts: Dict[str, int] = defaultdict(int)
    cell_handles: Dict[str, object] = {}

    def _get_handle(cell_key: str):
        if cell_key not in cell_handles:
            path = os.path.join(OUTPUT_DIR, f"{cell_key}.unsorted.jsonl")
            cell_handles[cell_key] = open(path, "w", encoding="utf-8")
        return cell_handles[cell_key]

    papers_processed = 0
    refs_processed = 0
    refs_written = 0

    try:
        for row in _iter_interleaved(*input_files):
            bibcode, result_path, n_total, n_matched, n_uncertain, n_unmatched, fmt, disc = row

            # Early stop: all cells at cap
            if all(cell_counts.get(k, 0) >= v for k, v in caps.items()):
                logger.info("All cells at cap — stopping early after %d papers",
                            papers_processed)
                break

            # Pre-filter: skip if all cells this paper could contribute to are full
            discipline = DISCIPLINE_MAP.get(disc, disc)
            candidate_cells = _candidate_cells_for(discipline, fmt)
            if candidate_cells and all(
                cell_counts.get(c, 0) >= caps.get(c, 0)
                for c in candidate_cells
            ):
                continue

            if not os.path.isfile(result_path):
                continue

            references = _parse_result_file(result_path, fmt)
            papers_processed += 1
            refs_processed += len(references)

            for ref in references:
                candidate = _build_candidate(
                    ref, bibcode, fmt, disc,
                    n_total, n_matched, n_uncertain, n_unmatched,
                )
                if candidate is None:
                    continue

                cell_key = _cell_key(
                    candidate.reference.discipline,
                    candidate.reference.parse_method,
                )

                if cell_counts.get(cell_key, 0) >= caps.get(cell_key, 0):
                    continue

                candidate.reference.id = str(uuid.uuid4())
                candidate.candidate.id = str(uuid.uuid4())
                candidate.priority_score = compute_priority_score(candidate)

                fh = _get_handle(cell_key)
                fh.write(json.dumps(candidate.to_dict(), ensure_ascii=False) + "\n")
                cell_counts[cell_key] += 1
                refs_written += 1

            if papers_processed % LOG_INTERVAL == 0:
                logger.info(
                    "Papers: %d | Refs: %d | Written: %d",
                    papers_processed, refs_processed, refs_written,
                )

    finally:
        for fh in cell_handles.values():
            fh.close()

    logger.info("Pass complete. Papers: %d | Refs: %d | Written: %d",
                papers_processed, refs_processed, refs_written)

    final_counts: Dict[str, int] = {}
    for cell_key in cell_counts:
        unsorted = os.path.join(OUTPUT_DIR, f"{cell_key}.unsorted.jsonl")
        sorted_path = os.path.join(OUTPUT_DIR, f"{cell_key}.jsonl")
        n = _sort_jsonl_by_priority(unsorted, sorted_path)
        os.remove(unsorted)
        final_counts[cell_key] = n
        logger.info("Cell %s: %d candidates → %s", cell_key, n, sorted_path)

    _write_summary(final_counts)
    return final_counts


def _iter_single_file(path: str) -> Iterator[Tuple]:
    """
    Yields one parsed row at a time from a single input file.
    Never loads the full file into memory.
    """
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 8:
                if lineno <= 10 or lineno % 1_000_000 == 0:
                    logger.warning("Line %d in %s: expected 8 fields, got %d — skipping",
                                   lineno, os.path.basename(path), len(parts))
                continue
            bibcode, result_path, n_total, n_matched, n_uncertain, n_unmatched, fmt, disc = parts
            try:
                yield (
                    bibcode.strip(),
                    result_path.strip(),
                    int(n_total), int(n_matched),
                    int(n_uncertain), int(n_unmatched),
                    fmt.strip().upper(),
                    disc.strip().upper(),
                )
            except ValueError:
                continue


def _iter_interleaved(*paths: str) -> Iterator[Tuple]:
    """
    Interleaves rows from multiple input files in round-robin order.
    Exhausted files are dropped; iteration continues until all files
    are exhausted or the caller breaks early.

    Round-robin ensures that no single format can fill all cells before
    the others have had a chance to contribute — which is the key property
    needed when the three files differ greatly in size.

    Example with 3 files of sizes 4M, 3M, 0.5M:
      draw 1 row from file 0, 1 from file 1, 1 from file 2,
      draw 1 row from file 0, 1 from file 1, 1 from file 2, ...
      once file 2 is exhausted, continue round-robin over files 0 and 1.
    """
    if len(paths) == 1:
        # Fast path: single file — no interleaving overhead
        yield from _iter_single_file(paths[0])
        return

    iterators = [_iter_single_file(p) for p in paths]
    active = list(range(len(iterators)))   # indices of non-exhausted iterators

    while active:
        next_active = []
        for idx in active:
            try:
                yield next(iterators[idx])
                next_active.append(idx)
            except StopIteration:
                logger.info("Input file exhausted: %s", os.path.basename(paths[idx]))
        active = next_active


def _candidate_cells_for(discipline: str, fmt: str) -> List[str]:
    """Returns cell keys this paper could contribute to (for pre-filtering)."""
    if fmt in ("TEXT", "OCR"):
        return [f"{discipline}__unstructured"]
    return [
        f"{discipline}__element_citation",
        f"{discipline}__mixed_citation",
        f"{discipline}__unstructured",
    ]


_HEADER_RE = re.compile(r"^---<(.+?)>---$")
_REF_LINE_RE = re.compile(r"^(\d+)\s+(\S+)\s+--\s+(.*)$")


def _parse_result_file(path: str, fmt: str) -> List[Dict]:
    results = []
    citing_bibcode: Optional[str] = None

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")

            header_match = _HEADER_RE.match(line)
            if header_match:
                citing_bibcode = header_match.group(1)
                continue

            ref_match = _REF_LINE_RE.match(line)
            if ref_match and citing_bibcode:
                status = int(ref_match.group(1))
                raw_bibcode = ref_match.group(2).strip()
                raw_string = ref_match.group(3).strip()

                matched_bibcode: Optional[str] = None
                if status in (MATCH_STATUS["matched"], MATCH_STATUS["uncertain"]):
                    if not re.fullmatch(r"\.+", raw_bibcode):
                        matched_bibcode = raw_bibcode

                results.append({
                    "citing_bibcode": citing_bibcode,
                    "status": status,
                    "matched_bibcode": matched_bibcode,
                    "raw_string": raw_string,
                    "source_format": fmt,
                    "source_filename": path,
                })

    return results


def _build_candidate(
    ref: Dict,
    source_bibcode: str,
    fmt: str,
    disc: str,
    n_total: int,
    n_matched: int,
    n_uncertain: int,
    n_unmatched: int,
) -> Optional[GoldCandidate]:
    if not ref["raw_string"].strip():
        return None

    status = ref["status"]

    parse_method, source_provenance = classify_parse_method(
        fmt, ref["raw_string"], ref["matched_bibcode"]
    )
    pub_type, pub_type_confidence = classify_publication_type(
        parse_method, ref["raw_string"], fmt
    )
    field_presence = _extract_field_presence(ref["raw_string"], parse_method)

    if status == MATCH_STATUS["matched"]:
        ads_confidence = 0.90
        sampling_slice = SamplingSlice.HIGH_CONFIDENCE
        negative_type = None
    elif status == MATCH_STATUS["uncertain"]:
        ads_confidence = 0.55
        sampling_slice = SamplingSlice.MID_CONFIDENCE
        negative_type = "near_miss"
    else:
        ads_confidence = 0.0
        sampling_slice = SamplingSlice.NO_MATCH
        negative_type = "true_no_match"

    ref_instance = ReferenceInstance(
        id="",
        reference_string=ref["raw_string"],
        source_document_id=source_bibcode,
        source_filename=ref.get("source_filename", ""),
        parse_method=parse_method,
        source_provenance=source_provenance,
        discipline=DISCIPLINE_MAP.get(disc, disc),
        publication_type=pub_type,
        publication_type_confidence=pub_type_confidence,
        field_presence=field_presence,
        ads_confidence_score=ads_confidence,
        sampling_slice=sampling_slice,
        article_n_total=n_total,
        article_n_matched=n_matched,
        article_n_uncertain=n_uncertain,
        article_n_unmatched=n_unmatched,
    )
    candidate_rec = CandidateRecord(
        id="",
        scix_id=None,
        bibcode=ref["matched_bibcode"],
        negative_type=negative_type,
        needs_adjudication=(status == MATCH_STATUS["uncertain"]),
    )
    return GoldCandidate(
        reference=ref_instance,
        candidate=candidate_rec,
        match_status=status,
    )


_FIELD_TAGS_XML = {
    "author":   [".//surname", ".//person-group"],
    "year":     [".//year"],
    "journal":  [".//source"],
    "volume":   [".//volume"],
    "page":     [".//fpage", ".//page"],
    "doi":      [".//pub-id[@pub-id-type='doi']"],
    "arxiv_id": [".//pub-id[@pub-id-type='arxiv']"],
    "title":    [".//article-title", ".//chapter-title"],
}

_FIELD_PATTERNS_TEXT = {
    "year":     re.compile(r"\b(19|20)\d{2}\b"),
    "volume":   re.compile(r"\b(?:vol\.?\s*|volume\s*)\d+", re.IGNORECASE),
    "page":     re.compile(r"\b(?:pp?\.?\s*)?\d{1,5}[-\u2013]\d{1,5}"),
    "doi":      re.compile(r"10\.\d{4,}/\S+"),
    "arxiv_id": re.compile(r"arXiv[:\s]\s*\d{4}\.\d{4,}", re.IGNORECASE),
}


def _extract_field_presence(
    raw_string: str, parse_method: ParseMethod
) -> Dict[str, bool]:
    presence = {f: False for f in
                ["author", "year", "journal", "volume", "page",
                 "doi", "arxiv_id", "title"]}

    if parse_method in (ParseMethod.ELEMENT_CITATION, ParseMethod.MIXED_CITATION):
        try:
            root = ET.fromstring(f"<root>{raw_string}</root>")
            for field, xpaths in _FIELD_TAGS_XML.items():
                for xp in xpaths:
                    el = root.find(xp)
                    if el is not None and (el.text or "").strip():
                        presence[field] = True
                        break
        except ET.ParseError:
            pass

    if parse_method == ParseMethod.UNSTRUCTURED or not any(presence.values()):
        presence["year"]     = bool(_FIELD_PATTERNS_TEXT["year"].search(raw_string))
        presence["volume"]   = bool(_FIELD_PATTERNS_TEXT["volume"].search(raw_string))
        presence["page"]     = bool(_FIELD_PATTERNS_TEXT["page"].search(raw_string))
        presence["doi"]      = bool(_FIELD_PATTERNS_TEXT["doi"].search(raw_string))
        presence["arxiv_id"] = bool(_FIELD_PATTERNS_TEXT["arxiv_id"].search(raw_string))
        presence["author"]   = bool(re.search(r"[A-Z][a-z]+,\s+[A-Z]", raw_string))
        presence["title"]    = len(raw_string) > 80

    return presence


def _sort_jsonl_by_priority(unsorted_path: str, sorted_path: str) -> int:
    records = []
    with open(unsorted_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r.get("priority_score", 0.0), reverse=True)
    tmp_path = sorted_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp_path, sorted_path)
    return len(records)


def _write_summary(counts: Dict[str, int]) -> None:
    summary_path = os.path.join(OUTPUT_DIR, "sampling_summary.json")
    targets_flat = {}
    for (disc, pm), (lo, hi) in TARGETS.items():
        key = f"{disc}__{pm}"
        targets_flat[key] = {
            "target_min": lo, "target_max": hi,
            "candidates": counts.get(key, 0),
        }
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(targets_flat, fh, indent=2)
    logger.info("Summary written to %s", summary_path)


def _cell_key(discipline: str, parse_method: ParseMethod) -> str:
    return f"{discipline}__{parse_method.value}"

def _sort_inputs_by_size(paths: List[str]) -> List[str]:
    """
    Returns input file paths sorted by descending line count.

    Putting the largest file first ensures that parse-method cells which
    only that format can fill (e.g. element_citation from XML) start
    accumulating candidates in round 1 of the interleaver rather than
    waiting for smaller files to contribute their (irrelevant) row first.
    Line counting is done with a fast byte-scan rather than reading the
    full file into memory.
    """
    def _count_lines(path: str) -> int:
        count = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                count += chunk.count(b"\n")
        return count

    sized = [(p, _count_lines(p)) for p in paths]
    sized.sort(key=lambda x: x[1], reverse=True)
    for path, n in sized:
        logger.info("Input file %s: %d lines", os.path.basename(path), n)
    return [p for p, _ in sized]




if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if len(sys.argv) < 2:
        print(
            "Usage: python sampler.py <file1> [<file2> ...]\n"
            "Files are sorted by line count automatically — order does not matter.\n"
            "Example:\n"
            "  python sampler.py citing2file_labeled_XML_shuffled.tsv "
            "citing2file_labeled_TXT_shuffled.tsv "
            "citing2file_labeled_OCR_shuffled.tsv",
            file=sys.stderr,
        )
        sys.exit(1)
    input_files = sys.argv[1:]
    for f in input_files:
        if not os.path.isfile(f):
            print(f"ERROR: input file not found: {f}", file=sys.stderr)
            sys.exit(1)
    # File order does not matter — run() sorts by descending line count automatically.
    result = run(*input_files)
    print("\nCandidates per cell:")
    for cell, n in sorted(result.items()):
        print(f"  {cell}: {n}")
