#!/usr/bin/env python3
"""Build a deterministic, complete HolderPro corresponding-source archive."""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

from fetch_dependency_sources import verify_dependency_source_directory


PRUSA_COMMIT = "b028299c770b8380ee81c921a2867d522f288123"
PRUSA_REQUIRED = "src/libslic3r/Support/OrganicSupport.cpp"


def run(*command: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        command, cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def tracked(root: Path) -> list[str]:
    raw = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    result = [
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    ]
    if not result:
        raise RuntimeError(
            "repository has no tracked files; corresponding source must come from a commit"
        )
    return sorted(result)


def forbidden(path: str, patterns: list[str]) -> bool:
    return any(
        Path(path).match(pattern) or fnmatch.fnmatchcase(path, pattern)
        for pattern in patterns
    )


def validate_dependency_manifest(
    path: Path, requirements_path: Path, holderpro_commit: str
) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "holderpro.dependency-sources/v1":
        raise RuntimeError("dependency manifest has the wrong schema")
    if document.get("holderpro_build_id") != holderpro_commit:
        raise RuntimeError(
            "dependency manifest is bound to a different HolderPro commit"
        )
    if document.get("prusa_source_commit") != PRUSA_COMMIT:
        raise RuntimeError(
            "dependency manifest is bound to a different PrusaSlicer commit"
        )
    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise RuntimeError("dependency manifest has no components")
    names = {item.get("name") for item in components if isinstance(item, dict)}
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    required_names = {item["name"] for item in requirements.get("components", [])}
    # Qt is a runtime shipped by the pinned PySide6 wheel, not a Python
    # distribution of its own, and is added from the reviewed binary lock.
    required_names.add("Qt")
    for required in required_names:
        if required not in names:
            raise RuntimeError(f"dependency manifest is missing {required}")
    for item in components:
        if (
            not isinstance(item, dict)
            or not item.get("name")
            or not item.get("version")
            or not item.get("license")
        ):
            raise RuntimeError(f"incomplete dependency manifest entry: {item!r}")
        if not item.get("source_commit") and not (
            item.get("source_url") and item.get("source_sha256")
        ):
            raise RuntimeError(
                f"dependency has no verifiable source locator: {item.get('name')}"
            )
        if (
            item.get("ecosystem") == "pypi"
            and item.get("release_constraint") != f"=={item.get('version')}"
        ):
            raise RuntimeError(
                f"dependency is not bound to its release constraint: {item.get('name')}"
            )
    return document


def copy_git_tree(
    source: Path,
    destination: Path,
    temporary: Path,
    archive_name: str,
) -> None:
    status = run("git", "status", "--porcelain", "--untracked-files=all", cwd=source)
    if status:
        raise RuntimeError(f"{archive_name} source checkout is not clean")
    archive_path = temporary / f"{archive_name}-source.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", "--output", str(archive_path), "HEAD"],
        cwd=source,
        check=True,
    )
    destination.mkdir(parents=True)
    with tarfile.open(archive_path, "r:") as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe {archive_name} archive path: {member.name}")
            if member.issym() or member.islnk():
                target = Path(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    raise RuntimeError(
                        f"unsafe {archive_name} archive link: "
                        f"{member.name} -> {member.linkname}"
                    )
        archive.extractall(destination, members=members, filter="data")


def write_manifest(root: Path) -> None:
    records = []
    directories: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"source staging tree contains a link: {relative}")
        if path.is_dir():
            directories.add(relative)
        elif path.is_file() and relative != "SOURCE-MANIFEST.sha256":
            records.append(f"{digest(path)}  {path.relative_to(root).as_posix()}\n")
        else:
            raise RuntimeError(
                f"source staging tree contains a special file: {relative}"
            )
    expected_directories: set[str] = set()
    for record in records:
        relative = record.split("  ", 1)[1].rstrip("\n")
        parent = Path(relative).parent
        while parent != Path("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if directories != expected_directories:
        extra = sorted(directories - expected_directories)
        raise RuntimeError(
            "source staging tree contains unmanifested empty directories: "
            + ", ".join(extra)
        )
    (root / "SOURCE-MANIFEST.sha256").write_text("".join(records), encoding="utf-8")


def normalized_tar(source: Path, destination: Path, epoch: int) -> None:
    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = epoch
        return info

    with tarfile.open(destination, "w", dereference=False) as archive:
        archive.add(source, arcname=source.name, recursive=True, filter=normalize)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--prusa-source", type=Path, required=True)
    parser.add_argument("--dependency-manifest", type=Path, required=True)
    parser.add_argument("--dependency-source-directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repository = args.repository.resolve()
    prusa = args.prusa_source.resolve()
    output_path = args.output.resolve()
    if run("git", "rev-parse", "HEAD", cwd=prusa) != PRUSA_COMMIT:
        raise SystemExit(f"PrusaSlicer source is not pinned commit {PRUSA_COMMIT}")
    if not (prusa / PRUSA_REQUIRED).is_file():
        raise SystemExit(
            f"PrusaSlicer checkout is incomplete: missing {PRUSA_REQUIRED}"
        )
    holderpro_commit = run("git", "rev-parse", "HEAD", cwd=repository)
    dependency_document = validate_dependency_manifest(
        args.dependency_manifest,
        repository / "packaging/dependency-source-requirements.json",
        holderpro_commit,
    )
    dependency_source_directory = args.dependency_source_directory.absolute()
    verify_dependency_source_directory(
        dependency_source_directory,
        dependency_document,
    )

    source_rules = tomllib.loads(
        (repository / "packaging/source-manifest.toml").read_text(encoding="utf-8")
    )
    if source_rules.get("schema") != 1:
        raise SystemExit("packaging/source-manifest.toml has the wrong schema")
    paths = tracked(repository)
    required_paths = source_rules.get("required_paths")
    if not isinstance(required_paths, list) or any(
        not isinstance(path, str) or not path for path in required_paths
    ):
        raise SystemExit("packaging/source-manifest.toml has invalid required_paths")
    missing_required = sorted(set(required_paths) - set(paths))
    if missing_required:
        raise SystemExit(
            "required source paths are not tracked:\n- " + "\n- ".join(missing_required)
        )
    fixture_roots = [
        item.rstrip("/") for item in source_rules.get("synthetic_fixture_roots", [])
    ]
    bad = [
        path
        for path in paths
        if forbidden(path, source_rules["forbidden_globs"])
        and not any(
            path == item or path.startswith(item + "/") for item in fixture_roots
        )
    ]
    if bad:
        raise SystemExit("forbidden tracked source paths:\n- " + "\n- ".join(bad))

    epoch_text = os.environ.get("SOURCE_DATE_EPOCH")
    epoch = (
        int(epoch_text)
        if epoch_text
        else int(run("git", "show", "-s", "--format=%ct", "HEAD", cwd=repository))
    )
    archive_root_name = f"holderpro-{args.version}-corresponding-source"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="holderpro-source-") as temporary:
        staging = Path(temporary) / archive_root_name
        holderpro = staging / "source/holderpro"
        copy_git_tree(repository, holderpro, Path(temporary), "holderpro")
        copy_git_tree(
            prusa,
            staging / "source/prusaslicer",
            Path(temporary),
            "prusaslicer",
        )
        (staging / "dependency-sources.json").write_text(
            json.dumps(dependency_document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        archived_dependencies = staging / "dependency-source-archives"
        shutil.copytree(dependency_source_directory, archived_dependencies)
        # Verify the copied bytes as well as the input tree so a concurrent input
        # change cannot silently enter the release archive.
        verify_dependency_source_directory(archived_dependencies, dependency_document)
        build_inputs = {
            "schema": "holderpro.build-inputs/v1",
            "holderpro_commit": holderpro_commit,
            "prusa_commit": PRUSA_COMMIT,
            "source_date_epoch": epoch,
            "version": args.version,
        }
        (staging / "BUILD-INPUTS.json").write_text(
            json.dumps(build_inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_manifest(staging)
        raw_tar = Path(temporary) / f"{archive_root_name}.tar"
        normalized_tar(staging, raw_tar, epoch)
        if output_path.name.endswith(".tar.zst"):
            subprocess.run(
                ["zstd", "-q", "-19", "-f", str(raw_tar), "-o", str(output_path)],
                check=True,
            )
        elif output_path.name.endswith(".tar.gz"):
            with raw_tar.open("rb") as source, output_path.open("wb") as target:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=target, mtime=epoch
                ) as compressed:
                    shutil.copyfileobj(source, compressed)
        else:
            raise SystemExit("output must end in .tar.zst or .tar.gz")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
