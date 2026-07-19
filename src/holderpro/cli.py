"""HolderPro command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import GenerationJob, GenerationResult, generate
from .diagnostics import export_diagnostics, run_doctor
from .engine import EngineError, EngineNotFoundError, find_engine, inspect_engine
from .errors import GenerationError
from .version import __version__

COMMANDS = frozenset({"generate", "doctor", "version"})


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", type=Path, help="reference STL/OBJ/3MF model")
    parser.add_argument("output", type=Path, help="support-only STL destination")
    parser.add_argument("--bottom-height", type=float, default=25.0, metavar="MM")
    parser.add_argument("--rotate-x", type=float, default=0.0, metavar="DEG")
    parser.add_argument("--rotate-y", type=float, default=0.0, metavar="DEG")
    parser.add_argument("--rotate-z", type=float, default=0.0, metavar="DEG")
    parser.add_argument("--layer-height", type=float, default=0.30, metavar="MM")
    parser.add_argument("--branch-diameter", type=float, default=2.0, metavar="MM")
    parser.add_argument(
        "--branch-diameter-angle", type=float, default=15.0, metavar="DEG"
    )
    parser.add_argument("--tip-diameter", type=float, default=0.8, metavar="MM")
    parser.add_argument("--branch-angle", type=float, default=40.0, metavar="DEG")
    parser.add_argument(
        "--branch-angle-slow", type=float, default=25.0, metavar="DEG"
    )
    parser.add_argument(
        "--contact-distance",
        type=float,
        default=0.0,
        metavar="MM",
        help="vertical model/support separation; 0 makes a bearing contact",
    )
    parser.add_argument(
        "--no-network-base",
        "--no-single-trunk",
        action="store_true",
        help="omit the single fused Organic trunk blob",
    )
    parser.add_argument("--base-thickness", type=float, default=20.0, metavar="MM")
    parser.add_argument(
        "--base-beam-width", "--blob-margin", type=float, default=3.0, metavar="MM"
    )
    parser.add_argument(
        "--base-node-diameter",
        "--root-lobe-diameter",
        type=float,
        default=8.0,
        metavar="MM",
    )
    parser.add_argument("--engine", type=Path, help="native adapter executable")
    parser.add_argument(
        "--retain-failed-geometry",
        action="store_true",
        help=(
            "on STL serialization failure, retain private model-derived debug "
            "files in a unique temporary directory"
        ),
    )
    parser.add_argument(
        "--result-json",
        action="store_true",
        help="print the structured generation result as JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="holderpro",
        description=(
            "Generate printable stands with HolderPro's pinned PrusaSlicer "
            "Organic-support engine."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"HolderPro {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    generate_parser = subparsers.add_parser(
        "generate", help="generate a filled support-only STL"
    )
    _add_generation_arguments(generate_parser)

    doctor_parser = subparsers.add_parser(
        "doctor", help="check engine, permissions, GUI, and graphics support"
    )
    doctor_parser.add_argument(
        "--output-dir",
        type=Path,
        help="directory in which generated STL files will be written",
    )
    doctor_parser.add_argument("--engine", type=Path, help="native adapter executable")
    doctor_parser.add_argument("--json", action="store_true", help="print JSON")
    doctor_parser.add_argument(
        "--export", type=Path, metavar="FILE", help="write a diagnostic JSON bundle"
    )
    doctor_parser.add_argument(
        "--no-redact-paths",
        action="store_true",
        help="include full local paths in diagnostics",
    )

    version_parser = subparsers.add_parser(
        "version", help="show HolderPro and native engine versions"
    )
    version_parser.add_argument("--engine", type=Path, help="native adapter executable")
    version_parser.add_argument("--json", action="store_true", help="print JSON")
    return parser


def _generation_job(args: argparse.Namespace) -> GenerationJob:
    """Construct the same validated job model used by the desktop UI."""

    return GenerationJob(
        input_path=args.input,
        output_path=args.output,
        bottom_height_mm=args.bottom_height,
        rotation_x_deg=args.rotate_x,
        rotation_y_deg=args.rotate_y,
        rotation_z_deg=args.rotate_z,
        layer_height_mm=args.layer_height,
        branch_diameter_mm=args.branch_diameter,
        branch_diameter_angle_deg=args.branch_diameter_angle,
        tip_diameter_mm=args.tip_diameter,
        branch_angle_deg=args.branch_angle,
        branch_angle_slow_deg=args.branch_angle_slow,
        contact_distance_mm=args.contact_distance,
        network_base_enabled=not args.no_network_base,
        base_thickness_mm=args.base_thickness,
        base_beam_width_mm=args.base_beam_width,
        base_node_diameter_mm=args.base_node_diameter,
        retain_failed_geometry=args.retain_failed_geometry,
        engine_path=args.engine,
    )


def _result_payload(result: GenerationResult) -> dict[str, Any]:
    return {
        "output_path": str(result.output_path),
        "engine": (
            result.engine_info.to_dict()
            if result.engine_info is not None
            else {"path": str(result.engine_path), "version": result.engine_version}
        ),
        "layer_count": result.layer_count,
        "component_count": result.component_count,
        "triangle_count": result.triangle_count,
        "volume_mm3": result.volume_mm3,
        "elapsed_seconds": result.elapsed_seconds,
        "base_node_count": result.base_node_count,
        "warnings": list(result.warnings),
    }


def _generate_command(args: argparse.Namespace) -> int:
    try:
        result = generate(
            _generation_job(args),
            progress=lambda message: print(message, file=sys.stderr),
        )
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.result_json:
        print(json.dumps(_result_payload(result), indent=2, sort_keys=True))
    else:
        print(result.output_path)
    return 0


def _doctor_command(args: argparse.Namespace) -> int:
    report = run_doctor(output_dir=args.output_dir, engine_path=args.engine)
    redact = not args.no_redact_paths
    if args.export:
        exported = export_diagnostics(
            args.export, report=report, redact_paths=redact
        )
        print(f"Diagnostic bundle: {exported}", file=sys.stderr)
    rendered = (
        report.to_json(redact_paths=redact)
        if args.json
        else report.to_text(redact_paths=redact)
    )
    print(rendered)
    return 0 if report.ok else 1


def _version_command(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {
        "product": {"name": "HolderPro", "version": __version__},
        "engine": None,
    }
    engine_error: str | None = None
    try:
        engine_payload = inspect_engine(find_engine(args.engine)).to_dict()
        # ``version --json`` is intended for public bug reports. Provenance is
        # useful there; an absolute installation path containing a user name is
        # not.
        engine_payload.pop("path", None)
        payload["engine"] = engine_payload
    except (EngineNotFoundError, EngineError):
        engine_error = (
            "HolderPro Organic engine unavailable; run `holderpro doctor` for "
            "redacted diagnostics"
        )
        payload["engine_error"] = engine_error
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"HolderPro {__version__}")
        engine = payload["engine"]
        if isinstance(engine, dict):
            adapter = engine.get("adapter_version") or "unknown"
            prusa = engine.get("prusaslicer_version") or "unavailable"
            print(f"Engine {adapter}; PrusaSlicer {prusa}")
        else:
            print(f"Engine unavailable: {engine_error}")
    return 0


def _normalize_generate_argv(argv: list[str]) -> list[str]:
    # Accept ``input output`` as a concise alias while presenting subcommands in
    # all documentation.
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        return ["generate", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        build_parser().print_help()
        return 0
    args = build_parser().parse_args(_normalize_generate_argv(arguments))
    if args.command == "generate":
        return _generate_command(args)
    if args.command == "doctor":
        return _doctor_command(args)
    if args.command == "version":
        return _version_command(args)
    build_parser().print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
