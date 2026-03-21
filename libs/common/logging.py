"""Structured JSON logging via structlog."""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(
    agent_name: str,
    level: str = "INFO",
    json_output: bool = True,
) -> structlog.stdlib.BoundLogger:
    """Configure structured logging for an agent.

    Args:
        agent_name: Name of the agent (bound to every log line).
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, output JSON lines. If False, human-readable.

    Returns:
        A bound logger with agent_name pre-set.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    logger: structlog.stdlib.BoundLogger = structlog.get_logger(agent_name)
    return logger.bind(agent_name=agent_name)
