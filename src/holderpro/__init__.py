"""HolderPro's public Organic-support generation API.

The API is intentionally compact: construct a :class:`GenerationJob`, pass it
to :func:`generate`, and inspect the returned :class:`GenerationResult`.
Expected failures use the exported exception hierarchy so applications can
distinguish invalid jobs, cancellation, engine failures, and geometry failures.
"""

from .engine import EngineInfo
from .errors import (
    EngineError,
    EngineExecutionError,
    EngineNotFoundError,
    EngineProvenanceError,
    GenerationCancelled,
    GenerationError,
    GenerationValidationError,
    HolderProError,
)
from .runner import GenerationJob, GenerationResult, generate
from .version import __version__

__all__ = [
    "EngineError",
    "EngineExecutionError",
    "EngineInfo",
    "EngineNotFoundError",
    "EngineProvenanceError",
    "GenerationCancelled",
    "GenerationError",
    "GenerationJob",
    "GenerationResult",
    "GenerationValidationError",
    "HolderProError",
    "__version__",
    "generate",
]
