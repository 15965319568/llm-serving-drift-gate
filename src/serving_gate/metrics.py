from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Iterable


class BoundedMetrics:
    """Small in-process registry with a fixed label vocabulary and series cap."""

    # Starter defect: request_id creates unbounded metric series.
    ALLOWED_LABELS = frozenset({"model_family", "model_version", "status", "scenario", "request_id"})
    MAX_SERIES = 32
    BUCKETS_MS = (50, 100, 250, 500, 1000, 2000, 5000)

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
        self._histograms: defaultdict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = defaultdict(
            lambda: [0] * (len(self.BUCKETS_MS) + 1)
        )

    def _key(self, name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        unknown = set(labels) - self.ALLOWED_LABELS
        if unknown:
            raise ValueError(f"unbounded metric label(s): {sorted(unknown)}")
        return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def inc(self, name: str, labels: dict[str, str] | None = None, amount: int = 1) -> None:
        key = self._key(name, labels or {})
        with self._lock:
            if key not in self._counters and self.series_count >= self.MAX_SERIES:
                raise ValueError("metric series limit exceeded")
            self._counters[key] += amount

    def observe_ms(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels or {})
        with self._lock:
            if key not in self._histograms and self.series_count >= self.MAX_SERIES:
                raise ValueError("metric series limit exceeded")
            bucket = len(self.BUCKETS_MS)
            for index, boundary in enumerate(self.BUCKETS_MS):
                if value <= boundary:
                    bucket = index
                    break
            self._histograms[key][bucket] += 1

    @property
    def series_count(self) -> int:
        return len(set(self._counters) | set(self._histograms))

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": [
                    {"name": name, "labels": dict(labels), "value": value}
                    for (name, labels), value in sorted(self._counters.items())
                ],
                "histograms": [
                    {"name": name, "labels": dict(labels), "buckets_ms": list(self.BUCKETS_MS), "counts": counts}
                    for (name, labels), counts in sorted(self._histograms.items())
                ],
                "series_count": self.series_count,
            }
