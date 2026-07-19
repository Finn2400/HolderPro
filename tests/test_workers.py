from __future__ import annotations

from pathlib import Path
import sys

import pytest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
pytest.importorskip("PySide6")

from holderpro.workers import DiagnosticsWorker, GenerationWorker  # noqa: E402


def test_late_cancel_after_atomic_success_is_reported_as_finished() -> None:
    result = object()
    emitted: list[tuple[str, object | None]] = []
    worker: GenerationWorker

    def generate(_job: object, **_callbacks: object) -> object:
        worker.cancel()
        return result

    worker = GenerationWorker(object(), generate_fn=generate)
    worker.finished.connect(lambda value: emitted.append(("finished", value)))
    worker.cancelled.connect(lambda: emitted.append(("cancelled", None)))

    worker.run()

    assert emitted == [("finished", result)]


def test_diagnostics_worker_uses_requested_output_directory(tmp_path: Path) -> None:
    report = object()
    observed: list[Path] = []
    emitted: list[object] = []

    def diagnose(*, output_dir: Path) -> object:
        observed.append(output_dir)
        return report

    worker = DiagnosticsWorker(tmp_path, diagnostics_fn=diagnose)
    worker.finished.connect(emitted.append)

    worker.run()

    assert observed == [tmp_path]
    assert emitted == [report]
