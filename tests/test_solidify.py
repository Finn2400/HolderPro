from __future__ import annotations

import io
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import trimesh


PROJECT_PYTHON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_PYTHON))

from holderpro.solidify import (  # noqa: E402
    FORMAT_VERSION,
    LayerFormatError,
    SolidificationError,
    export_mesh_stl,
    export_stl,
    load_layer_document,
    regularize_tangent_components_for_stl,
    solidify_layers,
)


def square(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def document(*layers: dict) -> dict:
    return {"version": FORMAT_VERSION, "layers": list(layers)}


def layer(
    *,
    print_z: float = 1.0,
    height: float = 1.0,
    polygons: list[dict] | None = None,
) -> dict:
    return {
        "print_z": print_z,
        "height": height,
        "polygons": polygons if polygons is not None else [],
    }


def polygon(contour: list[list[float]], holes: list[list[list[float]]] | None = None) -> dict:
    return {"contour": contour, "holes": holes if holes is not None else []}


def test_loads_mapping_json_text_file_and_stream(tmp_path: Path) -> None:
    raw = document(layer(polygons=[polygon(square(0, 0, 2, 3))]))
    json_text = json.dumps(raw)
    json_path = tmp_path / "layers.json"
    json_path.write_text(json_text, encoding="utf-8")

    loaded = [
        load_layer_document(raw),
        load_layer_document(json_text),
        load_layer_document(json_path),
        load_layer_document(str(json_path)),
        load_layer_document(io.BytesIO(json_text.encode("utf-8"))),
    ]

    assert all(item == loaded[0] for item in loaded)
    assert loaded[0].layers[0].bottom_z == pytest.approx(0.0)


def test_extrudes_print_z_as_layer_top_and_unions_overlapping_slabs() -> None:
    # The second slab overlaps the first in both Z and XY. A concatenation would
    # have volume 200; the boolean union must have volume 150.
    raw = document(
        layer(print_z=1.0, height=1.0, polygons=[polygon(square(0, 0, 10, 10))]),
        layer(print_z=1.5, height=1.0, polygons=[polygon(square(0, 0, 10, 10))]),
    )

    mesh = solidify_layers(raw)

    assert mesh.is_watertight
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(150.0)
    np.testing.assert_allclose(mesh.bounds[:, 2], [0.0, 1.5], atol=1e-12)


def test_holes_are_respected_regardless_of_input_winding() -> None:
    outer_clockwise = list(reversed(square(0, 0, 10, 10)))
    hole_counter_clockwise = square(3, 3, 7, 7)
    raw = document(
        layer(
            print_z=2.0,
            height=2.0,
            polygons=[polygon(outer_clockwise, [hole_counter_clockwise])],
        )
    )

    mesh = solidify_layers(raw)

    assert mesh.is_volume
    assert mesh.volume == pytest.approx((100.0 - 16.0) * 2.0)
    assert len(mesh.split()) == 1


def test_overlapping_polygons_are_unioned_within_a_layer() -> None:
    raw = document(
        layer(
            polygons=[
                polygon(square(0, 0, 2, 2)),
                polygon(square(1, 0, 3, 2)),
            ]
        )
    )

    mesh = solidify_layers(raw)

    assert mesh.is_volume
    assert mesh.volume == pytest.approx(6.0)


def test_adjacent_layers_with_changing_footprints_form_one_solid() -> None:
    raw = document(
        layer(
            print_z=0.2,
            height=0.2,
            polygons=[polygon(square(0, 0, 4, 4))],
        ),
        layer(
            print_z=0.4,
            height=0.2,
            polygons=[polygon(square(1, 1, 3, 3))],
        ),
    )

    mesh = solidify_layers(raw)

    assert mesh.is_volume
    assert mesh.volume == pytest.approx(4.0)
    assert len(mesh.split()) == 1


def test_decimal_roundoff_does_not_separate_adjacent_layers() -> None:
    # In binary64, 0.8 - 0.2 is slightly greater than 0.6. The interchange
    # values still describe adjacent 0.2 mm layers and must not create two
    # shells separated by a 1e-16 mm gap.
    raw = document(
        layer(
            print_z=0.6,
            height=0.2,
            polygons=[polygon(square(0, 0, 4, 4))],
        ),
        layer(
            print_z=0.8,
            height=0.2,
            polygons=[polygon(square(1, 1, 3, 3))],
        ),
    )

    mesh = solidify_layers(raw)

    assert mesh.is_volume
    assert len(mesh.split()) == 1


def test_disconnected_supports_are_each_closed_volume() -> None:
    raw = document(
        layer(
            polygons=[
                polygon(square(0, 0, 1, 1)),
                polygon(square(3, 0, 4, 1)),
            ]
        )
    )

    mesh = solidify_layers(raw)

    assert mesh.is_watertight
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(2.0)
    assert len(mesh.split()) == 2


def test_export_stl_is_binary_watertight_and_positive_volume(tmp_path: Path) -> None:
    raw = document(
        layer(print_z=0.4, height=0.2, polygons=[polygon(square(-2, -1, 2, 1))])
    )
    output = tmp_path / "nested" / "supports.stl"

    returned = export_stl(raw, output)
    reloaded = trimesh.load_mesh(output, file_type="stl", process=True)

    assert returned == output
    assert output.read_bytes()[:5] != b"solid"
    assert isinstance(reloaded, trimesh.Trimesh)
    assert reloaded.is_watertight
    assert reloaded.is_volume
    assert reloaded.volume == pytest.approx(1.6, rel=1e-6)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"version": 2, "layers": []}, "version must be 1"),
        ({"version": 1}, "layers must be an array"),
        (document(layer(height=0.0)), "height must be greater than zero"),
        (
            document(layer(print_z=float("nan"))),
            "print_z must be a finite number",
        ),
        (
            document(layer(polygons=[{"holes": []}])),
            "contour is required",
        ),
        (
            document(layer(polygons=[polygon([[0, 0], [1, 0], [2, 0]])])),
            "must enclose a non-zero area",
        ),
    ],
)
def test_rejects_malformed_documents(raw: dict, message: str) -> None:
    with pytest.raises(LayerFormatError, match=message):
        load_layer_document(raw)


def test_empty_document_cannot_be_exported_as_a_fake_surface() -> None:
    with pytest.raises(SolidificationError, match="no filled support polygons"):
        solidify_layers(document(layer(polygons=[])))


def test_export_requires_stl_extension(tmp_path: Path) -> None:
    raw = document(layer(polygons=[polygon(square(0, 0, 1, 1))]))
    with pytest.raises(ValueError, match=r"end in \.stl"):
        export_stl(raw, tmp_path / "supports.obj")


def test_export_uses_ascii_when_binary_float32_collapses_geometry(tmp_path: Path) -> None:
    raw = document(
        layer(
            polygons=[
                polygon(square(49.999999, 0.0, 50.000001, 1.0)),
            ]
        )
    )
    output = tmp_path / "collapsed.stl"

    export_stl(raw, output)
    reloaded = trimesh.load_mesh(output, file_type="stl", process=True)

    assert output.read_bytes().startswith(b"solid organic_supports\n")
    assert reloaded.is_watertight
    assert reloaded.is_volume
    assert reloaded.volume == pytest.approx(2e-6, rel=2e-3)


def test_printable_validator_accepts_tangent_closed_shells(tmp_path: Path) -> None:
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((1.0, 1.0, 0.0))
    source = trimesh.util.concatenate((left, right))
    output = tmp_path / "tangent-shells.stl"
    validated: list[Path] = []

    returned = export_mesh_stl(
        source,
        output,
        printable_validator=lambda candidate: validated.append(candidate) or True,
    )

    strict_reload = trimesh.load_mesh(output, file_type="stl", process=True)
    assert validated
    assert not output.read_bytes().startswith(b"solid organic_supports\n")
    assert not strict_reload.is_volume
    assert returned.is_watertight
    assert returned.is_volume
    assert returned.volume == pytest.approx(2.0)


def test_export_microscopically_unions_tangent_shells_when_validator_rejects(
    tmp_path: Path,
) -> None:
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((1.0, 1.0, 0.0))
    source = trimesh.util.concatenate((left, right))
    output = tmp_path / "regularized-tangent-shells.stl"

    returned = export_mesh_stl(
        source,
        output,
        printable_validator=lambda _candidate: False,
    )

    reloaded = trimesh.load_mesh(output, file_type="stl", process=True)
    assert reloaded.is_watertight and reloaded.is_volume
    assert returned.is_watertight and returned.is_volume
    assert returned.volume == pytest.approx(2.0, abs=0.02)
    np.testing.assert_allclose(returned.bounds[0, 2], -0.5, atol=1e-7)


def test_export_separates_coincident_vertex_fans_without_losing_volume(
    tmp_path: Path,
) -> None:
    left = trimesh.creation.box()
    right = trimesh.creation.box()
    right.apply_translation((1.0, 1.0, 0.0))
    source = trimesh.util.concatenate((left, right))
    output = tmp_path / "separated-vertex-fans.stl"

    returned = export_mesh_stl(
        source,
        output,
        printable_validator=lambda _candidate: False,
    )

    reloaded = trimesh.load_mesh(output, file_type="stl", process=True)
    assert reloaded.is_watertight and reloaded.is_volume
    assert returned.volume == pytest.approx(source.volume, abs=0.02)


def test_regularization_preserves_inward_nested_cavity_volume() -> None:
    outer = trimesh.creation.box(extents=(4.0, 4.0, 4.0))
    inner = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
    inner.faces = inner.faces[:, ::-1]
    source = trimesh.util.concatenate((outer, inner))

    regularized = regularize_tangent_components_for_stl(source)

    assert source.volume == pytest.approx(56.0)
    assert regularized.is_watertight and regularized.is_volume
    assert regularized.volume == pytest.approx(56.0, abs=0.02)
