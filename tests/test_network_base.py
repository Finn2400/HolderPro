from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union


PROJECT_PYTHON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_PYTHON))

from holderpro.network_base import add_network_base  # noqa: E402
from holderpro.runner import _count_connected_components  # noqa: E402
from holderpro.solidify import solidify_layers  # noqa: E402


def _square(x: float, y: float, size: float = 2.0) -> list[list[float]]:
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size]]


def test_network_base_connects_all_bed_roots_into_one_solid() -> None:
    payload = {
        "version": 1,
        "layers": [
            {
                "print_z": print_z,
                "height": 0.3,
                "polygons": [
                    {"contour": _square(0.0, 0.0), "holes": []},
                    {"contour": _square(10.0, 0.0), "holes": []},
                    {"contour": _square(5.0, 8.0), "holes": []},
                ],
            }
            for print_z in (0.3, 0.6, 0.9, 1.2)
        ],
    }

    based, stats = add_network_base(
        payload,
        thickness_mm=1.2,
        beam_width_mm=2.0,
        node_diameter_mm=4.0,
    )
    mesh = solidify_layers(based)

    assert stats.node_count == 3
    assert based["layers"][0]["kind"] == "integrated_single_organic_trunk"
    assert len(based["layers"][0]["polygons"]) == 1
    assert based["layers"][0]["trunk_fullness"] == 1.0
    assert based["layers"][-1]["trunk_linear_fullness"] == pytest.approx(0.25)
    assert based["layers"][-1]["trunk_fullness"] == pytest.approx(0.15625)
    assert all(
        len(layer["polygons"]) == 1
        for layer in based["layers"]
        if layer.get("kind") == "integrated_single_organic_trunk"
    )
    assert mesh.is_watertight and mesh.is_volume
    assert _count_connected_components(mesh) == 1
    np.testing.assert_allclose(mesh.bounds[:, 2], (0.0, 1.2), atol=1e-8)


def test_network_base_rejects_nonpositive_blob_margin() -> None:
    payload = {
        "version": 1,
        "layers": [
            {
                "print_z": 0.3,
                "height": 0.3,
                "polygons": [{"contour": _square(0.0, 0.0), "holes": []}],
            }
        ],
    }

    with pytest.raises(ValueError, match="must be positive"):
        add_network_base(
            payload,
            thickness_mm=1.0,
            beam_width_mm=0.0,
            node_diameter_mm=4.0,
        )


def test_tall_trunk_footprint_eases_continuously_into_native_branches() -> None:
    native_polygons = [
        {"contour": _square(0.0, 0.0), "holes": []},
        {"contour": _square(10.0, 0.0), "holes": []},
        {"contour": _square(5.0, 8.0), "holes": []},
    ]
    payload = {
        "version": 1,
        "layers": [
            {
                "print_z": index * 0.3,
                "height": 0.3,
                "polygons": native_polygons,
            }
            for index in range(1, 69)
        ],
    }

    based, _stats = add_network_base(
        payload,
        thickness_mm=20.0,
        beam_width_mm=3.0,
        node_diameter_mm=8.0,
    )

    areas = [
        unary_union(
            [Polygon(item["contour"], item["holes"]) for item in layer["polygons"]]
        ).area
        for layer in based["layers"]
    ]
    native_area = sum(Polygon(item["contour"]).area for item in native_polygons)
    assert areas[0] > areas[20] > areas[40] > areas[55] >= native_area
    assert areas[-1] == pytest.approx(native_area)
    assert based["layers"][-2]["trunk_connector_width_mm"] >= 1.2
    assert all(
        len(layer["polygons"]) == 1
        for layer in based["layers"]
        if layer.get("kind") == "integrated_single_organic_trunk"
    )
