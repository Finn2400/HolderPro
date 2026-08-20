"""Repair small layer-to-layer gaps that detach Organic support material."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import nearest_points, unary_union

from .solidify import FilledPolygon, load_layer_document


@dataclass(frozen=True, slots=True)
class ConnectivityRepairStats:
    bridge_count: int
    unresolved_regions: int
    nearest_unresolved_gap_mm: float | None


def _shape(polygon: FilledPolygon) -> Polygon:
    return Polygon(polygon.contour, polygon.holes)


def _parts(geometry: Any) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [
        item for item in getattr(geometry, "geoms", ()) if isinstance(item, Polygon)
    ]


def _serialize(geometry: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for polygon in _parts(geometry):
        if polygon.is_empty or polygon.area <= 1e-12:
            continue
        result.append(
            {
                "contour": [
                    [float(x), float(y)] for x, y in polygon.exterior.coords[:-1]
                ],
                "holes": [
                    [[float(x), float(y)] for x, y in ring.coords[:-1]]
                    for ring in polygon.interiors
                    if Polygon(ring).area > 1e-12
                ],
            }
        )
    return result


def _rounded_bridge(left: Any, right: Any, diameter_mm: float) -> Any:
    left_point, right_point = nearest_points(left, right)
    return LineString((left_point, right_point)).buffer(
        diameter_mm * 0.5,
        quad_segs=12,
        cap_style="round",
        join_style="round",
    )


def repair_layer_connectivity(
    payload: Mapping[str, Any],
    *,
    reach_mm: float,
    bridge_diameter_mm: float,
) -> tuple[dict[str, Any], ConnectivityRepairStats]:
    """Bridge detached layer regions back to material reachable from the bed.

    The repair follows the solid upward one layer at a time. A region is
    reachable when it overlaps reachable material in the layer immediately
    below. Newly detached regions are joined with a rounded web only when their
    XY gap is within ``reach_mm``. This preserves ordinary branch splitting:
    every branch that overlaps the common layer below remains independently
    reachable and receives no bridge.
    """

    reach_mm = float(reach_mm)
    bridge_diameter_mm = float(bridge_diameter_mm)
    if not math.isfinite(reach_mm) or reach_mm <= 0.0:
        raise ValueError("connectivity repair reach must be positive and finite")
    if not math.isfinite(bridge_diameter_mm) or bridge_diameter_mm <= 0.0:
        raise ValueError("connectivity bridge diameter must be positive and finite")

    document = load_layer_document(payload)
    result = dict(payload)
    repaired_layers: list[dict[str, Any]] = []
    previous_reachable: Any | None = None
    previous_top: float | None = None
    found_bed_seed = False
    bridge_count = 0
    unresolved_regions = 0
    nearest_unresolved: float | None = None

    for raw_layer, layer in zip(payload["layers"], document.layers, strict=True):
        updated = dict(raw_layer)
        if not layer.polygons:
            repaired_layers.append(updated)
            continue

        layer_shape = unary_union([_shape(item) for item in layer.polygons])
        layer_parts = [item for item in _parts(layer_shape) if item.area > 1e-12]
        vertical_gap = (
            max(0.0, layer.bottom_z - previous_top)
            if previous_top is not None
            else 0.0
        )
        has_reachable_below = (
            previous_reachable is not None and previous_top is not None
        )

        if not found_bed_seed:
            # The first occupied layer is the bed seed. A network base should
            # already make it one region; if not, preserve the largest region
            # as the reachable seed and let the normal bridge logic repair the
            # remaining bed roots within the configured reach.
            found_bed_seed = True
            seed = max(layer_parts, key=lambda item: item.area)
            reachable_parts: list[Any] = [seed]
            pending = [item for item in layer_parts if item is not seed]
            anchor: Any = seed
        else:
            assert has_reachable_below
            assert previous_reachable is not None
            reachable_parts = []
            pending = []
            for part in layer_parts:
                overlap = float(part.intersection(previous_reachable).area)
                if overlap > 1e-12 and vertical_gap <= reach_mm + 1e-9:
                    reachable_parts.append(part)
                else:
                    pending.append(part)
            anchor = previous_reachable

        added_bridges: list[Any] = []
        for part in pending:
            xy_gap = float(part.distance(anchor))
            gap = math.hypot(xy_gap, vertical_gap)
            if gap <= reach_mm + 1e-9:
                bridge = _rounded_bridge(anchor, part, bridge_diameter_mm)
                added_bridges.append(bridge)
                reachable_parts.append(part)
                anchor = unary_union((anchor, part, bridge))
                bridge_count += 1
            else:
                unresolved_regions += 1
                nearest_unresolved = (
                    gap
                    if nearest_unresolved is None
                    else min(nearest_unresolved, gap)
                )

        if vertical_gap > 1e-6 and reachable_parts:
            assert previous_reachable is not None
            current_reachable = unary_union(reachable_parts)
            overlap_columns = previous_reachable.intersection(current_reachable)
            connector_shape = unary_union((overlap_columns, *added_bridges))
            serialized_connector = _serialize(connector_shape)
            if serialized_connector:
                repaired_layers.append(
                    {
                        "print_z": float(layer.bottom_z),
                        "height": float(vertical_gap),
                        "polygons": serialized_connector,
                        "kind": "connectivity_repair_vertical_connector",
                    }
                )
                bridge_count += 1

        if added_bridges:
            layer_shape = unary_union((layer_shape, *added_bridges))
            serialized = _serialize(layer_shape)
            if not serialized:
                raise ValueError("connectivity repair produced an empty layer")
            updated["polygons"] = serialized
            updated["connectivity_repair_bridge_count"] = len(added_bridges)

        previous_reachable = (
            unary_union((*reachable_parts, *added_bridges))
            if reachable_parts
            else None
        )
        previous_top = layer.print_z if previous_reachable is not None else None
        repaired_layers.append(updated)

    result["layers"] = repaired_layers
    result["connectivity_repair"] = {
        "reach_mm": reach_mm,
        "bridge_diameter_mm": bridge_diameter_mm,
        "bridge_count": bridge_count,
        "unresolved_regions": unresolved_regions,
        "nearest_unresolved_gap_mm": nearest_unresolved,
    }
    return result, ConnectivityRepairStats(
        bridge_count=bridge_count,
        unresolved_regions=unresolved_regions,
        nearest_unresolved_gap_mm=nearest_unresolved,
    )


__all__ = ["ConnectivityRepairStats", "repair_layer_connectivity"]
