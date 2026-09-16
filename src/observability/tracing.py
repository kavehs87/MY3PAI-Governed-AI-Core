"""Structured NDJSON logging and OpenTelemetry-compatible trace export.

Logs are one JSON object per line (NDJSON) with monotonic sequence numbers.
Spans follow OTel field naming (trace_id, span_id, start/end timestamps) so
standard collectors accept them without translation.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class NdjsonLogger:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._seq = itertools.count()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self, level: str, event: str, attributes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            seq = next(self._seq)
        record: dict[str, Any] = {
            "seq": seq,
            "level": level,
            "event": event,
            "wall_time": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "attributes": attributes or {},
        }
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record


class OtelSpanExporter:
    """Minimal OTel-compatible span writer (JSON array file)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: list[dict[str, Any]] = []

    def start_span(
        self, trace_id: str, name: str, attributes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        span: dict[str, Any] = {
            "trace_id": trace_id,
            "span_id": f"{time.monotonic_ns():016x}",
            "name": name,
            "start_time_unix_nano": time.time_ns(),
            "attributes": attributes or {},
        }
        return span

    def end_span(self, span: dict[str, Any], status: str = "OK") -> dict[str, Any]:
        finished = dict(span)
        finished["end_time_unix_nano"] = time.time_ns()
        finished["status"] = status
        with self._lock:
            self._spans.append(finished)
        return finished

    def spans(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._spans)

    def write_file(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = list(self._spans)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
