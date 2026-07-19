"""Public exception hierarchy for HolderPro generation."""

from __future__ import annotations


class HolderProError(RuntimeError):
    """Base class for expected HolderPro operational failures."""


class GenerationError(HolderProError):
    """A generation job could not produce a validated printable support solid."""


class GenerationValidationError(GenerationError):
    """A generation job is incomplete, inconsistent, or outside valid ranges."""


class GenerationCancelled(GenerationError):
    """Generation was cancelled by the caller before an output was committed."""


class EngineError(GenerationError):
    """The native HolderPro engine could not be located, queried, or run."""


class EngineNotFoundError(EngineError):
    """No usable bundled, configured, source-tree, or PATH engine was found."""


class EngineProvenanceError(EngineError):
    """An engine identified itself as an unsupported or unpinned build."""


class EngineExecutionError(EngineError):
    """The native engine failed while processing a generation job."""


__all__ = [
    "EngineError",
    "EngineExecutionError",
    "EngineNotFoundError",
    "EngineProvenanceError",
    "GenerationCancelled",
    "GenerationError",
    "GenerationValidationError",
    "HolderProError",
]
