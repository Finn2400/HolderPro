"""Shared mesh-loading operations used by the GUI and generation runner."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from .threemf import load_3mf_mesh


class MeshLoadError(ValueError):
    """A reference model could not be loaded as a finite triangle mesh."""


def load_reference_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load all geometry in *path* into one non-destructively processed mesh.

    The source face order is retained because HolderPro's painted support mask
    addresses triangles by index.
    """

    source = Path(path)
    try:
        if source.suffix.lower() == ".3mf":
            mesh = load_3mf_mesh(source)
        else:
            scene = trimesh.load_scene(source, process=False)
            mesh = scene.to_mesh()
    except Exception as exc:
        raise MeshLoadError(f"Could not load reference model {source}: {exc}") from exc
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise MeshLoadError("The reference model contains no triangle faces")
    if not np.all(np.isfinite(mesh.vertices)):
        raise MeshLoadError("The reference model contains non-finite coordinates")
    return mesh


__all__ = ["MeshLoadError", "load_reference_mesh"]
