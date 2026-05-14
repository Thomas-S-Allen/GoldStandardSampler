# Gold Standard Sampler

Produces a stratified ranked candidate list for the SciX reference matching
gold-standard dataset from historical ADS reference resolution data.

## Requirements

Python 3.8+, no third-party dependencies. All imports are from the standard
library (`json`, `re`, `xml.etree.ElementTree`, `uuid`, `logging`, etc.).

## Usage

```bash
# Run the sampler with one or more input files
python sampler.py <file1> [<file2> ...]

# Recommended: one file per format (XML, TEXT, OCR), each pre-shuffled
python sampler.py \
  citing2file_labeled_XML_shuffled.tsv \
  citing2file_labeled_TXT_shuffled.tsv \
  citing2file_labeled_OCR_shuffled.tsv

# Print a summary report of the candidate pool
python report.py [output_dir]

# Run the tests
python -m pytest tests/ -v
```

**Input file order does not matter.** The sampler automatically sorts the
provided files by descending line count before interleaving, so the largest
file always leads each round. You can pass the files in any order.

## Why three separate input files?

Splitting the input by format (XML, TEXT, OCR) before shuffling ensures that
all three formats contribute to the output cells simultaneously. Without this,
round-robin interleaving from a single large file risks filling cells with
whichever format dominates the front of the file before the others have
contributed meaningfully.

### Preparing the input files

Starting from a single combined input file, split and shuffle with standard
Unix tools:

```bash
# Split by format (column 7)
awk -F'\t' '$7 == "XML"' combined_input.tsv > citing2file_labeled_XML.tsv
awk -F'\t' '$7 == "TEXT"' combined_input.tsv > citing2file_labeled_TXT.tsv
awk -F'\t' '$7 == "OCR"' combined_input.tsv > citing2file_labeled_OCR.tsv

# Shuffle each (requires enough RAM to hold the file)
shuf citing2file_labeled_XML.tsv  > citing2file_labeled_XML_shuffled.tsv
shuf citing2file_labeled_TXT.tsv  > citing2file_labeled_TXT_shuffled.tsv
shuf citing2file_labeled_OCR.tsv  > citing2file_labeled_OCR_shuffled.tsv
```

## Input file format

A tab-separated file with eight columns per row:

| Column | Description |
|---|---|
| bibcode | Bibcode of the citing paper |
| result_path | Absolute path to the ADS reference resolution result file |
| n_total | Total references in the paper |
| n_matched | Number successfully matched (status 1) |
| n_uncertain | Number of questionable matches (status 5) |
| n_unmatched | Number with no match (status 0) |
| format | XML, TEXT, or OCR |
| discipline | AST, PS, HP, GEO, or MUL |

Example row:
```
1998CeMDA..72..245D\t/proj/ads/references/resolved/CeMDA/0072/1998CeMDA..72..245D.jats.xml.result\t185\t118\t18\t49\tXML\tAST
```

## Result file format

Each result file is a plain text file. A section starts with a header line:

```
---<bibcode_of_citing_paper>---
```

Each subsequent reference line:

```
STATUS  BIBCODE  --  RAW_STRING
```

Where `STATUS` is `0` (unmatched), `1` (matched), or `5` (questionable /
near-miss). For unmatched references the BIBCODE field contains dots.
For status-5 references the BIBCODE is the resolver's best candidate.

## How the sampler works

```
Input files (one per format, shuffled)
        |
        v
_sort_inputs_by_size()      sort files largest-first (automatic)
        |
        v
_iter_interleaved()         round-robin across all files simultaneously
        |                   -- OCR exhausts first, then TEXT, then XML
        |                   -- early stopping once all cells reach cap
        v
For each reference line:
  classify_parse_method()     element_citation / mixed_citation / unstructured
  classify_publication_type() JATS attr -> field presence -> heuristics
  _extract_field_presence()   boolean field flags
  _build_candidate()          assemble GoldCandidate objects
  compute_priority_score()    rank within each cell
        |
        v
output/{cell_key}.jsonl     one file per (discipline x parse_method) cell,
                            sorted by descending priority_score
output/sampling_summary.json
        |
        v
report.py                   human-readable summary + floor checks
```

## Early stopping and candidate caps

Once a cell accumulates `CANDIDATE_CAP_MULTIPLIER x target_max` candidates
(default: 50x), no further candidates are written to it. Once **all** cells
reach their cap, the main loop exits regardless of how many input lines remain.

With the actual ADS corpus file sizes:

| File | Lines |
|---|---|
| `citing2file_labeled_OCR_shuffled.tsv` | 124,522 |
| `citing2file_labeled_TXT_shuffled.tsv` | 426,144 |
| `citing2file_labeled_XML_shuffled.tsv` | 9,799,118 |

The OCR file exhausts first (after ~124k rounds). All cells are expected to
reach their caps well before the TXT file exhausts, so in practice only a
small fraction of the total input is processed. Expected runtime: minutes,
not hours.

## Output

One JSONL file per `(discipline, parse_method)` cell written to `output/`.
Records within each file are sorted by descending `priority_score`.

Cell naming convention: `{discipline}__{parse_method}.jsonl`

Example cells:
- `astrophysics__element_citation.jsonl`
- `earth_science__unstructured.jsonl`
- `planetary_science__mixed_citation.jsonl`

### Each JSONL record

```json
{
  "reference": {
    "id": "uuid",
    "reference_string": "...",
    "source_document_id": "bibcode_of_citing_paper",
    "source_filename": "/proj/ads/...",
    "parse_method": "element_citation|mixed_citation|unstructured",
    "source_provenance": "publisher_xml|arxiv|ocr|other",
    "discipline": "astrophysics|earth_science|heliophysics|planetary_science|multidisciplinary",
    "publication_type": "journal_article|conference_proceedings|thesis|book|report|preprint|...",
    "publication_type_confidence": "high|medium|low|unknown",
    "field_presence": {"author": true, "year": true, "journal": false, "...": "..."},
    "ads_confidence_score": 0.9,
    "sampling_slice": "high_confidence|mid_confidence|low_confidence|no_match",
    "article_n_total": 30,
    "article_n_matched": 24,
    "article_n_uncertain": 2,
    "article_n_unmatched": 4
  },
  "candidate": {
    "id": "uuid",
    "bibcode": "1993ArBB..303..208K",
    "scix_id": null,
    "negative_type": null,
    "needs_adjudication": false
  },
  "match_status": 1,
  "priority_score": 0.6300
}
```

`scix_id` is populated during database loading (not during sampling).
`negative_type` is `null` for positive matches, `"near_miss"` for status-5
pairs, and `"true_no_match"` for status-0 pairs.
`needs_adjudication` is `true` for all status-5 near-misses.

## Configuration (`config.py`)

| Constant | Default | Description |
|---|---|---|
| `OUTPUT_DIR` | `"output"` | Directory for output JSONL files |
| `CANDIDATE_CAP_MULTIPLIER` | `50` | Candidates per cell = this x target_max |
| `LOG_INTERVAL` | `10_000` | Papers between progress log lines |
| `WORKERS` | `1` | Reserved for future parallel processing |
| `TARGETS` | (see config) | (min, max) pair targets per cell |
| `SAMPLING_SLICE_THRESHOLDS` | `{high: 0.85, mid: 0.50}` | Confidence score cutoffs |
| `PUBLICATION_TYPE_HEURISTICS` | (see config) | Regex patterns for type inference |
| `PUB_TYPE_ABSOLUTE_MINIMUMS` | (see config) | Absolute floor counts per discipline |

## Module overview

| Module | Purpose |
|---|---|
| `sampler.py` | Main entry point — orchestrates the full workflow |
| `config.py` | All configurable constants and targets |
| `models.py` | Dataclasses: `ReferenceInstance`, `CandidateRecord`, `GoldCandidate` |
| `parse_method_classifier.py` | Determines element_citation / mixed_citation / unstructured |
| `publication_type_classifier.py` | Four-step cascade: JATS -> field presence -> heuristics -> default |
| `ranking.py` | Priority score computation for curator draw-down ordering |
| `report.py` | Summary report and absolute minimum floor checks |
| `tests/test_sampler.py` | 30 unit tests covering all modules |
