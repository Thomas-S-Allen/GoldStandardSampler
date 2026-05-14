"""
gold_standard_sampler/report.py
---------------------------------
Reads the JSONL candidate files produced by sampler.py and writes a
set of report files to a separate reports directory.

Reports written:
  cell_summary.txt          — candidate counts vs targets per cell
  pub_type_distribution.txt — publication type breakdown per cell
  floor_check.txt           — absolute minimum floor check per discipline
  report_index.txt          — index listing all reports and their paths

Usage:
    python report.py [data_dir] [report_dir]

    data_dir    directory containing the JSONL files  (default: OUTPUT_DIR)
    report_dir  directory to write report files into   (default: REPORT_DIR)

Both directories are created if they do not exist.
If data_dir exists but contains no JSONL files, a notice is written to
report_dir and the script exits cleanly.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from config import OUTPUT_DIR, PUB_TYPE_ABSOLUTE_MINIMUMS, REPORT_DIR, TARGETS

# Width used for all report tables
_W = 72


def run(data_dir: str = OUTPUT_DIR, report_dir: str = REPORT_DIR) -> None:
    """
    Main entry point.

    Parameters
    ----------
    data_dir   : directory containing sampler.py JSONL output files.
    report_dir : directory to write report files into.
    """
    # ── Ensure both directories exist ────────────────────────────────────
    _ensure_dir(data_dir)
    _ensure_dir(report_dir)

    # ── Load cells ────────────────────────────────────────────────────────
    cells = _load_cells(data_dir)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_meta = (
        f"Generated : {timestamp}\n"
        f"Data dir  : {os.path.abspath(data_dir)}\n"
        f"Report dir: {os.path.abspath(report_dir)}\n"
    )

    if not cells:
        notice = (
            f"{header_meta}\n"
            "No JSONL files found in data directory.\n"
            "Run sampler.py first to populate the data directory.\n"
        )
        _write(report_dir, "report_index.txt", notice)
        print(f"No JSONL files found in '{data_dir}'. "
              f"Notice written to '{report_dir}/report_index.txt'.")
        return

    # ── Write individual report files ─────────────────────────────────────
    reports = [
        ("cell_summary.txt",          _build_cell_summary(cells)),
        ("pub_type_distribution.txt", _build_pub_type_distribution(cells)),
        ("floor_check.txt",           _build_floor_check(cells)),
    ]

    index_lines = [header_meta, "Reports\n" + "-" * _W]
    for filename, body in reports:
        full_body = header_meta + "\n" + body
        _write(report_dir, filename, full_body)
        index_lines.append(f"  {filename:<35} {os.path.join(report_dir, filename)}")
        print(f"Written: {os.path.join(report_dir, filename)}")

    _write(report_dir, "report_index.txt", "\n".join(index_lines) + "\n")
    print(f"Written: {os.path.join(report_dir, 'report_index.txt')}")


# ── Directory helpers ──────────────────────────────────────────────────────

def _ensure_dir(path: str) -> None:
    """Creates directory if it does not exist; no-op if it does."""
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        print(f"Directory created: {path}")


def _write(report_dir: str, filename: str, content: str) -> None:
    """Writes content to report_dir/filename."""
    with open(os.path.join(report_dir, filename), "w", encoding="utf-8") as fh:
        fh.write(content)


# ── Loading ────────────────────────────────────────────────────────────────

def _load_cells(data_dir: str) -> Dict[str, List[dict]]:
    cells: Dict[str, List[dict]] = {}
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".jsonl"):
            continue
        cell_key = fname[:-6]
        rows = []
        with open(os.path.join(data_dir, fname), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        cells[cell_key] = rows
    return cells


# ── Cell summary ───────────────────────────────────────────────────────────

def _build_cell_summary(cells: Dict[str, List[dict]]) -> str:
    lines = [
        "CANDIDATE POOL vs TARGETS",
        "=" * _W,
        f"{'Cell':<40} {'Candidates':>10} {'Target (min-max)':>18} {'Status':>8}",
        "-" * _W,
    ]

    for (disc, pm), (tmin, tmax) in sorted(TARGETS.items()):
        if pm == "negatives":
            continue
        cell_key = f"{disc}__{pm}"
        n = len(cells.get(cell_key, []))
        status = "OK" if n >= tmin else ("LOW" if n > 0 else "EMPTY")
        lines.append(f"{cell_key:<40} {n:>10} {tmin:>8}\u2013{tmax:<8} {status:>8}")

    n_neg = sum(
        1
        for rows in cells.values()
        for row in rows
        if row["candidate"].get("negative_type") is not None
    )
    tmin, tmax = TARGETS[("all", "negatives")]
    status = "OK" if n_neg >= tmin else ("LOW" if n_neg > 0 else "EMPTY")
    lines.append(f"{'all__negatives':<40} {n_neg:>10} {tmin:>8}\u2013{tmax:<8} {status:>8}")
    lines.append("")
    return "\n".join(lines)


# ── Publication type distribution ──────────────────────────────────────────

def _build_pub_type_distribution(cells: Dict[str, List[dict]]) -> str:
    lines = [
        "PUBLICATION TYPE DISTRIBUTION (top 4 per cell)",
        "=" * _W,
    ]

    for cell_key, rows in sorted(cells.items()):
        if not rows:
            continue
        counts: Dict[str, int] = defaultdict(int)
        for row in rows:
            pt = row["reference"].get("publication_type", "unknown")
            counts[pt] += 1
        total = len(rows)
        top = sorted(counts.items(), key=lambda x: -x[1])[:4]
        top_str = "  ".join(
            f"{pt}: {n} ({100 * n // total}%)" for pt, n in top
        )
        lines.append(f"  {cell_key:<38} {top_str}")

    lines.append("")
    return "\n".join(lines)


# ── Absolute floor check ───────────────────────────────────────────────────

def _build_floor_check(cells: Dict[str, List[dict]]) -> str:
    lines = [
        "ABSOLUTE MINIMUM FLOOR CHECK (per discipline, all parse methods)",
        "=" * _W,
        f"{'Discipline':<22} {'Pub type':<25} {'Count':>6} {'Floor':>6} {'Status':>8}",
        "-" * _W,
    ]

    disciplines = sorted({
        disc for (disc, _) in TARGETS.keys() if disc != "all"
    })

    for disc in disciplines:
        disc_rows: List[dict] = []
        for cell_key, rows in cells.items():
            if cell_key.startswith(disc + "__"):
                disc_rows.extend(rows)

        for pub_type, floor in sorted(PUB_TYPE_ABSOLUTE_MINIMUMS.items()):
            n = sum(
                1 for row in disc_rows
                if row["reference"].get("publication_type") == pub_type
            )
            status = "OK" if n >= floor else "BELOW"
            lines.append(
                f"  {disc:<22} {pub_type:<25} {n:>6} {floor:>6} {status:>8}"
            )

    lines.append("")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if len(args) > 2:
        print(
            "Usage: python report.py [data_dir] [report_dir]\n"
            f"  data_dir   : directory containing JSONL files (default: {OUTPUT_DIR})\n"
            f"  report_dir : directory to write reports into  (default: {REPORT_DIR})",
            file=sys.stderr,
        )
        sys.exit(1)

    data_dir   = args[0] if len(args) >= 1 else OUTPUT_DIR
    report_dir = args[1] if len(args) >= 2 else REPORT_DIR

    run(data_dir, report_dir)
