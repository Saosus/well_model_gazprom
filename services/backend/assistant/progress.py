"""Per-request record of actual orchestrator stages (no fabricated progress)."""
from contextvars import ContextVar

calculation_stages: ContextVar[list | None] = ContextVar('calculation_stages', default=None)
