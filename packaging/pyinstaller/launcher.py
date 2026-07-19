"""Frozen desktop entry point kept outside the public package API."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from holderpro.ui import main


def _package_self_test() -> int:
    """Generate, validate, and render one connected solid with frozen code."""

    import numpy as np
    import trimesh
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer, vtkRenderWindow

    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 - registers backend

    from holderpro import GenerationJob, generate
    from holderpro.mesh_io import load_reference_mesh
    from holderpro.preview import load_support_preview_mesh
    from holderpro.surface_analysis import mesh_fingerprint

    with tempfile.TemporaryDirectory(prefix="holderpro-package-self-test-") as temporary:
        directory = Path(temporary)
        source = directory / "synthetic painted Ω reference.stl"
        output = directory / "synthetic connected Ω support.stl"
        trimesh.creation.box(extents=(10.0, 8.0, 2.0)).export(source)
        reference = load_reference_mesh(source)
        painted = tuple(
            int(index)
            for index in np.flatnonzero(reference.face_normals[:, 2] < -0.99)
        )
        if not painted:
            raise RuntimeError("synthetic package fixture has no downward facets")
        result = generate(
            GenerationJob(
                source,
                output,
                bottom_height_mm=8.0,
                layer_height_mm=0.4,
                branch_diameter_mm=2.4,
                tip_diameter_mm=1.2,
                painted_enforcer_faces=painted,
                paint_face_count=len(reference.faces),
                paint_mesh_fingerprint=mesh_fingerprint(reference),
                enforcers_only=True,
                network_base_enabled=True,
                base_thickness_mm=3.0,
                base_beam_width_mm=2.0,
                base_node_diameter_mm=5.0,
            )
        )
        support = load_support_preview_mesh(result.output_path)
        if result.component_count != 1 or result.base_node_count < 1:
            raise RuntimeError("frozen generation did not produce one connected base")
        if result.engine_info is None or not result.engine_info.verified:
            raise RuntimeError("frozen generation did not use a verified bundled engine")

        vertices = np.ascontiguousarray(support.vertices, dtype=np.float64)
        faces = np.asarray(support.faces, dtype=np.int64)
        points = vtkPoints()
        points.SetData(numpy_to_vtk(vertices, deep=True))
        cells_array = vtkCellArray()
        cells_array.SetData(
            numpy_to_vtkIdTypeArray(
                np.arange(0, 3 * (len(faces) + 1), 3, dtype=np.int64),
                deep=True,
            ),
            numpy_to_vtkIdTypeArray(faces.ravel(), deep=True),
        )
        polydata = vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetPolys(cells_array)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        actor = vtkActor()
        actor.SetMapper(mapper)
        renderer = vtkRenderer()
        renderer.AddActor(actor)
        window = vtkRenderWindow()
        window.SetOffScreenRendering(1)
        window.SetSize(64, 64)
        window.AddRenderer(renderer)
        window.Render()
        cells = int(polydata.GetNumberOfCells())
        visible = bool(actor.GetVisibility()) and bool(window.SupportsOpenGL())
        window.Finalize()
        if not visible or cells <= 0:
            raise RuntimeError("frozen viewer did not display the generated support mesh")

        print(
            json.dumps(
                {
                    "component_count": result.component_count,
                    "engine_verified": bool(
                        result.engine_info is not None and result.engine_info.verified
                    ),
                    "preview_cells": cells,
                    "triangle_count": len(support.faces),
                    "volume_mm3": float(support.volume),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--holderpro-opengl-probe":
        from holderpro.diagnostics import opengl_probe_main

        raise SystemExit(opengl_probe_main())
    if len(sys.argv) == 2 and sys.argv[1] == "--holderpro-package-self-test":
        raise SystemExit(_package_self_test())
    raise SystemExit(main())
