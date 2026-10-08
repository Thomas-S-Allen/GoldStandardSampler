"""Local web viewer for GoldStandardSampler JSONL output.

Usage:
    python3 viewer.py [output_dir] [--port 8000]
"""

from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import islice
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, urlparse


PAGE_SIZE = 24
MATCH_CATEGORIES = frozenset(("all", "matched", "near_miss", "no_match"))


class JsonlStore:
    """Read cell metadata and pages without loading whole JSONL files."""

    def __init__(self, output_dir: str) -> None:
        self.output_dir = Path(output_dir)
        self._count_cache: Dict[Path, Tuple[int, int, int]] = {}
        self._filter_cache: Dict[
            Path,
            Tuple[int, int, Dict[str, List[Tuple[int, int]]], int],
        ] = {}

    def cells(self) -> List[dict]:
        summary = self._load_summary()
        cells = []

        for path in sorted(self.output_dir.glob("*.jsonl")):
            cell_key = path.stem
            discipline, separator, parse_method = cell_key.partition("__")
            if not separator:
                discipline, parse_method = cell_key, "unknown"

            summary_row = summary.get(cell_key, {})
            count = summary_row.get("candidates")
            if not isinstance(count, int) or count < 0:
                count = self._count_lines(path)

            cells.append({
                "key": cell_key,
                "discipline": discipline,
                "parse_method": parse_method,
                "count": count,
                "target_min": summary_row.get("target_min"),
                "target_max": summary_row.get("target_max"),
            })

        return cells

    def page(self, cell_key: str, page_number: int,
             category: str = "all") -> dict:
        paths = {path.stem: path for path in self.output_dir.glob("*.jsonl")}
        path = paths.get(cell_key)
        if path is None:
            raise KeyError(f"Unknown cell: {cell_key}")
        if page_number < 1:
            raise ValueError("Page must be at least 1")
        if category not in MATCH_CATEGORIES:
            raise ValueError(f"Unknown match category: {category}")

        start = (page_number - 1) * PAGE_SIZE
        if category == "all":
            cells_by_key = {cell["key"]: cell for cell in self.cells()}
            count = cells_by_key[cell_key]["count"]
            records, ranks, invalid_records = self._unfiltered_page(path, start)
        else:
            offsets, invalid_records = self._filter_offsets(path)
            selected = offsets[category][start:start + PAGE_SIZE]
            count = len(offsets[category])
            records, ranks = self._read_offsets(path, selected)

        return {
            "cell": cell_key,
            "category": category,
            "page": page_number,
            "page_size": PAGE_SIZE,
            "total": count,
            "pages": max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE),
            "records": records,
            "ranks": ranks,
            "invalid_records": invalid_records,
        }

    def _unfiltered_page(self, path: Path, start: int) -> Tuple[List[dict], List[int], int]:
        records = []
        ranks = []
        invalid_records = 0
        with path.open(encoding="utf-8") as handle:
            for offset, line in enumerate(
                    islice(handle, start, start + PAGE_SIZE)):
                try:
                    records.append(json.loads(line))
                    ranks.append(start + offset + 1)
                except json.JSONDecodeError:
                    invalid_records += 1
        return records, ranks, invalid_records

    def _filter_offsets(
        self, path: Path
    ) -> Tuple[Dict[str, List[Tuple[int, int]]], int]:
        stat = path.stat()
        cached = self._filter_cache.get(path)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2], cached[3]

        offsets: Dict[str, List[Tuple[int, int]]] = {
            "matched": [], "near_miss": [], "no_match": [],
        }
        invalid_records = 0
        with path.open("rb") as handle:
            rank = 0
            while True:
                position = handle.tell()
                line = handle.readline()
                if not line:
                    break
                rank += 1
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    invalid_records += 1
                    continue

                status = record.get("match_status")
                category = "matched" if status == 1 else (
                    "near_miss" if status == 5 else "no_match"
                )
                offsets[category].append((position, rank))

        self._filter_cache[path] = (
            stat.st_mtime_ns, stat.st_size, offsets, invalid_records,
        )
        return offsets, invalid_records

    @staticmethod
    def _read_offsets(
        path: Path, offsets: List[Tuple[int, int]]
    ) -> Tuple[List[dict], List[int]]:
        records = []
        ranks = []
        with path.open("rb") as handle:
            for position, rank in offsets:
                handle.seek(position)
                records.append(json.loads(handle.readline()))
                ranks.append(rank)
        return records, ranks

    def _load_summary(self) -> dict:
        path = self.output_dir / "sampling_summary.json"
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
                return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _count_lines(self, path: Path) -> int:
        stat = path.stat()
        cached = self._count_cache.get(path)
        cache_key = (stat.st_mtime_ns, stat.st_size)
        if cached and cached[:2] == cache_key:
            return cached[2]

        count = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                count += chunk.count(b"\n")
        self._count_cache[path] = (cache_key[0], cache_key[1], count)
        return count


class ViewerHandler(BaseHTTPRequestHandler):
    store: JsonlStore
    html_path: Path

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._serve_html()
            return
        if parsed.path == "/api/cells":
            self._send_json({"cells": self.store.cells()})
            return
        if parsed.path == "/api/candidates":
            self._serve_candidates(parse_qs(parsed.query))
            return
        if parsed.path == "/favicon.ico":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_html(self) -> None:
        try:
            body = self.html_path.read_bytes()
        except OSError as error:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_candidates(self, query: dict) -> None:
        cell = query.get("cell", [""])[0]
        category = query.get("category", ["all"])[0]
        try:
            page_number = int(query.get("page", ["1"])[0])
            payload = self.store.page(cell, page_number, category)
        except (KeyError, ValueError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(payload)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def make_server(output_dir: str, host: str, port: int) -> ThreadingHTTPServer:
    store = JsonlStore(output_dir)
    html_path = Path(__file__).with_name("viewer.html")

    class ConfiguredHandler(ViewerHandler):
        pass

    ConfiguredHandler.store = store
    ConfiguredHandler.html_path = html_path
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Browse sampler JSONL output")
    parser.add_argument("output_dir", nargs="?", default="output")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if not os.path.isdir(args.output_dir):
        parser.error(f"output directory does not exist: {args.output_dir}")

    server = make_server(args.output_dir, args.host, args.port)
    address, port = server.server_address
    print(f"Viewing {os.path.abspath(args.output_dir)}")
    print(f"Open http://{address}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
