"""Public API for libs.tuner -- bounds registry, audit logging, YAML writer, and Claude integration."""

from __future__ import annotations

from libs.tuner.audit import ParameterChange, log_no_change, log_parameter_change
from libs.tuner.bounds import BoundsEntry, clip_value, load_bounds_registry, validate_value
from libs.tuner.claude_client import (
    DEFAULT_MODEL,
    TOOL_SCHEMA,
    build_system_prompt,
    build_user_message,
    call_claude,
)
from libs.tuner.recommender import TuningResult, run_tuning_cycle, validate_recommendation
from libs.tuner.writer import apply_parameter_changes

__all__ = [
    "BoundsEntry",
    "load_bounds_registry",
    "validate_value",
    "clip_value",
    "ParameterChange",
    "log_parameter_change",
    "log_no_change",
    "apply_parameter_changes",
    "TOOL_SCHEMA",
    "DEFAULT_MODEL",
    "build_system_prompt",
    "build_user_message",
    "call_claude",
    "TuningResult",
    "validate_recommendation",
    "run_tuning_cycle",
]
