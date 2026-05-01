"""
Prometheus Metrics — Change Review Orchestrator

Exposes counters and histograms for observability.
Degrades gracefully if prometheus_client is not installed.

Metrics:
  cro_reviews_total{recommendation}     — Counter
  cro_pipeline_duration_seconds         — Histogram (full pipeline)
  cro_agent_duration_seconds{agent}     — Histogram per agent
  cro_findings_total{severity}          — Counter per severity
  cro_llm_calls_total{outcome}          — Counter: success | fallback | error
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

_available = False

try:
    from prometheus_client import Counter, Histogram, start_http_server

    reviews_counter = Counter(
        "cro_reviews_total",
        "Total reviews processed",
        ["recommendation"],
    )
    pipeline_duration = Histogram(
        "cro_pipeline_duration_seconds",
        "Full pipeline execution duration",
        buckets=[1, 5, 10, 30, 60, 120],
    )
    agent_duration = Histogram(
        "cro_agent_duration_seconds",
        "Per-agent execution duration",
        ["agent"],
        buckets=[0.1, 0.5, 1, 5, 10, 30],
    )
    findings_counter = Counter(
        "cro_findings_total",
        "Total findings emitted",
        ["severity"],
    )
    llm_calls_counter = Counter(
        "cro_llm_calls_total",
        "LLM call outcomes",
        ["outcome"],
    )
    _available = True

except ImportError:
    # Stub no-ops so callers don't need to guard
    class _NoOp:
        def labels(self, **_): return self
        def inc(self, *_, **__): pass
        def observe(self, *_, **__): pass

    reviews_counter  = _NoOp()
    pipeline_duration = _NoOp()
    agent_duration   = _NoOp()
    findings_counter = _NoOp()
    llm_calls_counter = _NoOp()


@contextmanager
def timed_pipeline() -> Generator[None, None, None]:
    """Context manager that records pipeline_duration."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        pipeline_duration.observe(elapsed)


@contextmanager
def timed_agent(agent_name: str) -> Generator[None, None, None]:
    """Context manager that records agent_duration for a specific agent."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        agent_duration.labels(agent=agent_name).observe(elapsed)


def record_review_outcome(recommendation: str) -> None:
    reviews_counter.labels(recommendation=recommendation).inc()


def record_findings(severities: list[str]) -> None:
    for sev in severities:
        findings_counter.labels(severity=sev).inc()


def record_llm_outcome(outcome: str) -> None:
    """outcome: 'success' | 'fallback' | 'error'"""
    llm_calls_counter.labels(outcome=outcome).inc()
