from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
import trimesh

from holderpro.mesh_io import load_reference_mesh
import holderpro.threemf as threemf
from holderpro.threemf import ThreeMFError, load_3mf_mesh, write_3mf_mesh


RELATIONSHIPS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/model.model" Id="rel0"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _write_3mf(path: Path, model: str) -> Path:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("_rels/.rels", RELATIONSHIPS)
        archive.writestr("3D/model.model", model)
    return path


def test_loads_components_build_transforms_and_centimeter_units(tmp_path: Path) -> None:
    source = _write_3mf(
        tmp_path / "component scene.3mf",
        """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
       unit="centimeter">
  <resources>
    <object id="1" type="model"><mesh>
      <vertices>
        <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
        <vertex x="0" y="1" z="0"/>
      </vertices>
      <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
    </mesh></object>
    <object id="2" type="model"><components>
      <component objectid="1" transform="1 0 0 0 1 0 0 0 1 2 0 0"/>
    </components></object>
  </resources>
  <build>
    <item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 3 0"/>
    <item objectid="1"/>
  </build>
</model>
""",
    )

    mesh = load_reference_mesh(source)

    assert len(mesh.faces) == 2
    assert np.allclose(mesh.vertices[:3], ((20, 30, 0), (30, 30, 0), (20, 40, 0)))
    assert np.allclose(mesh.vertices[3:], ((0, 0, 0), (10, 0, 0), (0, 10, 0)))


def test_writer_preserves_triangle_order_duplicates_and_collinear_faces(
    tmp_path: Path,
) -> None:
    source = trimesh.creation.box(extents=(10.0, 8.0, 2.0))
    triangles = np.asarray(source.triangles, dtype=np.float32)
    triangles = np.concatenate(
        (
            triangles,
            triangles[:1],
            np.asarray([[[20.0, 0.0, 0.0], [21.0, 0.0, 0.0], [22.0, 0.0, 0.0]]]),
        )
    )
    vertices = triangles.reshape((-1, 3))
    faces = np.arange(len(vertices), dtype=np.int64).reshape((-1, 3))
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    destination = write_3mf_mesh(tmp_path / "painted posed reference.3mf", mesh)
    loaded = load_3mf_mesh(destination)

    assert len(loaded.faces) == len(mesh.faces) == 14
    np.testing.assert_array_equal(
        np.asarray(loaded.triangles, dtype=np.float32), triangles
    )


def test_rejects_component_cycles(tmp_path: Path) -> None:
    source = _write_3mf(
        tmp_path / "cycle.3mf",
        """<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model"><components><component objectid="2"/></components></object>
    <object id="2" type="model"><components><component objectid="1"/></components></object>
  </resources>
  <build><item objectid="1"/></build>
</model>""",
    )

    with pytest.raises(ThreeMFError, match="cycle"):
        load_3mf_mesh(source)


def test_rejects_out_of_range_triangle_index(tmp_path: Path) -> None:
    source = _write_3mf(
        tmp_path / "bad index.3mf",
        """<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources><object id="1" type="model"><mesh>
    <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
      <vertex x="0" y="1" z="0"/></vertices>
    <triangles><triangle v1="0" v2="1" v3="9"/></triangles>
  </mesh></object></resources>
  <build><item objectid="1"/></build>
</model>""",
    )

    with pytest.raises(ThreeMFError, match="out-of-range"):
        load_3mf_mesh(source)


def test_rejects_dtd_after_large_prefix(tmp_path: Path) -> None:
    source = _write_3mf(
        tmp_path / "late dtd.3mf",
        " " * 20_000
        + """<!DOCTYPE model [<!ENTITY expanded "not allowed">]>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources><object id="1" type="model"><mesh>
    <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
      <vertex x="0" y="1" z="0"/></vertices>
    <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
  </mesh></object></resources>
  <build><item objectid="1"/></build>
</model>""",
    )

    with pytest.raises(ThreeMFError, match="DTD or entity"):
        load_3mf_mesh(source)


def test_limits_exponential_component_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(threemf, "_MAX_EXPANDED_INSTANCES", 64)
    objects = [
        """<object id="1" type="model"><mesh>
        <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
          <vertex x="0" y="1" z="0"/></vertices>
        <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
        </mesh></object>"""
    ]
    for object_id in range(2, 12):
        child = object_id - 1
        objects.append(
            f"""<object id="{object_id}" type="model"><components>
            <component objectid="{child}"/><component objectid="{child}"/>
            </components></object>"""
        )
    source = _write_3mf(
        tmp_path / "exponential components.3mf",
        """<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
        <resources>"""
        + "".join(objects)
        + """</resources><build><item objectid="11"/></build></model>""",
    )

    with pytest.raises(ThreeMFError, match="expanded-instance limit"):
        load_3mf_mesh(source)


def test_rejects_suspicious_zip_compression_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(threemf, "_MAX_COMPRESSION_RATIO", 2.0)
    source = _write_3mf(
        tmp_path / "compressed bomb.3mf",
        """<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">"""
        + (" " * 20_000)
        + "</model>",
    )

    with pytest.raises(ThreeMFError, match="compression ratio"):
        load_3mf_mesh(source)
