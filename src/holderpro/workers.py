"""Qt worker for one cancelable organic-support generation job."""

from __future__ import annotations

from threading import Event
from typing import Any, Callable

try:  # Keep non-GUI tooling able to import the package without PySide6.
    from PySide6 import QtCore
except ImportError as exc:  # pragma: no cover - depends on the local environment
    QtCore = None  # type: ignore[assignment]
    _PYSIDE_IMPORT_ERROR: ImportError | None = exc
else:
    _PYSIDE_IMPORT_ERROR = None


GenerateFunction = Callable[..., Any]
DiagnosticsFunction = Callable[..., Any]


def require_pyside6() -> None:
    """Raise an actionable error if the optional desktop dependency is absent."""

    if QtCore is None:
        raise RuntimeError(
            "The organic-support desktop UI requires PySide6. "
            "Install the GUI dependencies and try again."
        ) from _PYSIDE_IMPORT_ERROR


if QtCore is not None:

    class GenerationWorker(QtCore.QObject):
        """Call the native runner away from Qt's UI thread.

        Cancellation is cooperative: the runner receives ``Event.is_set`` as
        its ``cancelled`` callback and is expected to check it between native
        processing stages.
        """

        progress = QtCore.Signal(str)
        finished = QtCore.Signal(object)
        failed = QtCore.Signal(str)
        cancelled = QtCore.Signal()

        def __init__(
            self,
            job: object,
            *,
            generate_fn: GenerateFunction | None = None,
        ) -> None:
            super().__init__()
            self._job = job
            self._generate_fn = generate_fn
            self._cancel_event = Event()

        @QtCore.Slot()
        def cancel(self) -> None:
            self._cancel_event.set()

        def _report_progress(self, message: str) -> None:
            self.progress.emit(str(message))

        @QtCore.Slot()
        def run(self) -> None:
            try:
                if self._generate_fn is None:
                    # Import lazily so the form can still open and report
                    # configuration errors if the native runner is unavailable.
                    from . import generate

                    generate_fn: GenerateFunction = generate
                else:
                    generate_fn = self._generate_fn

                result = generate_fn(
                    self._job,
                    progress=self._report_progress,
                    cancelled=self._cancel_event.is_set,
                )
            except Exception as exc:
                if self._cancel_event.is_set():
                    self.cancelled.emit()
                else:
                    message = str(exc).strip() or type(exc).__name__
                    self.failed.emit(message)
                return

            # A cancellation request arriving after the runner's atomic commit
            # cannot undo the completed STL. A successful return is therefore
            # always reported as success; cancellation is emitted from the
            # exception path above when generation actually stops early.
            self.finished.emit(result)


    class DiagnosticsWorker(QtCore.QObject):
        """Collect environment diagnostics away from Qt's UI thread."""

        finished = QtCore.Signal(object)
        failed = QtCore.Signal(str)

        def __init__(
            self,
            output_dir: object,
            *,
            diagnostics_fn: DiagnosticsFunction | None = None,
        ) -> None:
            super().__init__()
            self._output_dir = output_dir
            self._diagnostics_fn = diagnostics_fn

        @QtCore.Slot()
        def run(self) -> None:
            try:
                if self._diagnostics_fn is None:
                    from .diagnostics import run_doctor

                    diagnostics_fn: DiagnosticsFunction = run_doctor
                else:
                    diagnostics_fn = self._diagnostics_fn

                report = diagnostics_fn(output_dir=self._output_dir)
            except Exception as exc:
                self.failed.emit(str(exc).strip() or type(exc).__name__)
                return
            self.finished.emit(report)

else:

    class GenerationWorker:  # type: ignore[no-redef]  # pragma: no cover
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            require_pyside6()


    class DiagnosticsWorker:  # type: ignore[no-redef]  # pragma: no cover
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            require_pyside6()


__all__ = [
    "DiagnosticsFunction",
    "DiagnosticsWorker",
    "GenerateFunction",
    "GenerationWorker",
    "require_pyside6",
]
