from __future__ import annotations

from typing import Any

import pytest

from holderpro.connectivity_repair import repair_layer_connectivity
from holderpro.runner import _count_material_components
from holderpro.solidify import solidify_layers


def _square(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _polygon(
    x0: float, y0: float, x1: float, y1: float
) -> dict[str, Any]:
    return {"contour": _square(x0, y0, x1, y1), "holes": []}


def _layer(
    print_z: float, *polygons: dict[str, Any]
) -> dict[str, Any]:
    return {
        "print_z": print_z,
        "height": 1.0,
        "polygons": list(polygons),
    }


def test_small_detached_region_is_bridged_into_one_solid() -> None:
    payload = {
        "version": 1,
        "layers": [
            _layer(1.0, _polygon(0.0, 0.0, 2.0, 2.0)),
            _layer(
                2.0,
                _polygon(0.0, 0.0, 2.0, 2.0),
                _polygon(2.4, 0.0, 4.4, 2.0),
            ),
        ],
    }

    repaired, stats = repair_layer_connectivity(
        payload, reach_mm=0.5, bridge_diameter_mm=0.6
    )
    solid = solidify_layers(repaired)

    assert stats.bridge_count == 1
    assert stats.unresolved_regions == 0
    assert _count_material_components(solid) == 1


def test_gap_beyond_reach_remains_unresolved_and_is_reported() -> None:
    payload = {
        "version": 1,
        "layers": [
            _layer(1.0, _polygon(0.0, 0.0, 2.0, 2.0)),
            _layer(
                2.0,
                _polygon(0.0, 0.0, 2.0, 2.0),
                _polygon(2.4, 0.0, 4.4, 2.0),
            ),
        ],
    }

    repaired, stats = repair_layer_connectivity(
        payload, reach_mm=0.1, bridge_diameter_mm=0.6
    )

    assert stats.bridge_count == 0
    assert stats.unresolved_regions == 1
    assert stats.nearest_unresolved_gap_mm == pytest.approx(0.4)
    assert _count_material_components(solidify_layers(repaired)) == 2


def test_legitimate_branch_split_needs_no_horizontal_web() -> None:
    payload = {
        "version": 1,
        "layers": [
            _layer(1.0, _polygon(0.0, 0.0, 6.0, 2.0)),
            _layer(
                2.0,
                _polygon(0.0, 0.0, 2.5, 2.0),
                _polygon(3.5, 0.0, 6.0, 2.0),
            ),
        ],
    }

    repaired, stats = repair_layer_connectivity(
        payload, reach_mm=2.0, bridge_diameter_mm=0.6
    )

    assert stats.bridge_count == 0
    assert stats.unresolved_regions == 0
    assert "connectivity_repair_bridge_count" not in repaired["layers"][1]
    assert _count_material_components(solidify_layers(repaired)) == 1


def test_short_vertical_layer_dropout_gets_a_narrow_connector_slab() -> None:
    payload = {
        "version": 1,
        "layers": [
            _layer(1.0, _polygon(0.0, 0.0, 2.0, 2.0)),
            _layer(3.0, _polygon(0.5, 0.5, 1.5, 1.5)),
        ],
    }

    repaired, stats = repair_layer_connectivity(
        payload, reach_mm=1.1, bridge_diameter_mm=0.6
    )

    assert stats.unresolved_regions == 0
    assert stats.bridge_count == 1
    assert len(repaired["layers"]) == 3
    assert repaired["layers"][1]["kind"] == (
        "connectivity_repair_vertical_connector"
    )
    assert _count_material_components(solidify_layers(repaired)) == 1
