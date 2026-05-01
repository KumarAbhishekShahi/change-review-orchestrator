from __future__ import annotations
import logging
import sys
import os
def configure_logging(log_level: str = "INFO") -> None:
    import structlog
    fmt = os.getenv("LOG_FORMAT", "console").lower()
    level = os.getenv("LOG_LEVEL", log_level).upper()
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]
    if fmt == "json":
        processors = shared_processors + [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=False)]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.getLevelName(level))
