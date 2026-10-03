# coding=utf8
"""Shared result contracts for one batch and its finite phases."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INIT_FAILED = 2
EXIT_ALREADY_RUNNING = 3
EXIT_STOPPED = 130


@dataclass
class PhaseResult:
    name: str
    status: str = "completed"
    barrier_passed: bool = False
    cleanup_complete: bool = False
    output_healthy: bool = True
    enabled: bool = True
    unchanged: bool = False
    counts: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    init_failed: bool = False

    @property
    def recoverable_errors(self) -> bool:
        return self.status == "completed" and self.barrier_passed and bool(self.errors)

    @classmethod
    def disabled(cls, name: str) -> "PhaseResult":
        return cls(name=name, enabled=False, barrier_passed=True, cleanup_complete=True)

    def add_error(self, message: Any) -> None:
        self.errors.append(str(message))


@dataclass
class BatchResult:
    image: Optional[PhaseResult] = None
    novel: Optional[PhaseResult] = None
    errors: List[str] = field(default_factory=list)
    exit_code: int = EXIT_OK

    def add_error(self, message: Any) -> None:
        self.errors.append(str(message))

    def finalize(self) -> int:
        phases = [phase for phase in (self.image, self.novel) if phase is not None]
        if any(phase.status == "stopped" for phase in phases):
            self.exit_code = EXIT_STOPPED
        elif any(getattr(phase, "init_failed", False) for phase in phases):
            self.exit_code = EXIT_INIT_FAILED
        elif any(phase.status in ("failed",) or phase.errors for phase in phases) or self.errors:
            self.exit_code = EXIT_FAILED
        return self.exit_code
