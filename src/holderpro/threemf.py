"""Dependency-light 3MF geometry loading for HolderPro.

``trimesh`` delegates 3MF scene traversal to optional NetworkX and lxml
dependencies.  HolderPro only needs triangle geometry in deterministic build
order, so this module implements that narrow 3MF Core/Production path with the
Python standard library and NumPy.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path, PurePosixPath
from typing import IO
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import numpy as np
import trimesh


_UNIT_TO_MM = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}
_RELATIONSHIP_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_MAX_ARCHIVE_MEMBERS = 4096
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_MODEL_XML_BYTES = 256 * 1024 * 1024
_MAX_RELATIONSHIP_XML_BYTES = 4 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 1000.0
_MAX_OBJECTS = 100_000
_MAX_COMPONENTS = 500_000
_MAX_SOURCE_VERTICES = 5_000_000
_MAX_SOURCE_FACES = 5_000_000
_MAX_EXPANDED_INSTANCES = 100_000
_MAX_COMPONENT_DEPTH = 128
_MAX_OUTPUT_VERTICES = 5_000_000
_MAX_OUTPUT_FACES = 5_000_000


class ThreeMFError(ValueError):
    """A 3MF package cannot be represented as HolderPro triangle geometry."""


@dataclass(frozen=True, slots=True)
class _Component:
    member: str
    object_id: str
    transform: np.ndarray


@dataclass(frozen=True, slots=True)
class _Object:
    vertices: tuple[np.ndarray, ...]
    faces: tuple[np.ndarray, ...]
    components: tuple[_Component, ...]


@dataclass(frozen=True, slots=True)
class _Model:
    objects: dict[str, _Object]
    build: tuple[_Component, ...]


@dataclass(slots=True)
class _Budget:
    objects: int = 0
    components: int = 0
    source_vertices: int = 0
    source_faces: int = 0
    expanded_instances: int = 0
    output_vertices: int = 0
    output_faces: int = 0


class _DTDRejectingStream(io.RawIOBase):
    """Pass bytes through while rejecting XML declarations across read chunks."""

    def __init__(self, source: IO[bytes]) -> None:
        self._source = source
        self._tail = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self._source.read(size)
        combined = (self._tail + data).upper()
        if b"<!DOCTYPE" in combined or b"<!ENTITY" in combined:
            raise ThreeMFError(
                "3MF model XML may not contain a DTD or entity declaration"
            )
        self._tail = combined[-16:]
        return data


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _package_member(value: str, *, relative_to: str | None = None) -> str:
    """Normalize an OPC member path without permitting parent traversal."""

    raw = unquote(value).replace("\\", "/")
    if raw.startswith("/"):
        candidate = PurePosixPath(raw.lstrip("/"))
    elif relative_to is not None:
        candidate = PurePosixPath(relative_to).parent / raw
    else:
        candidate = PurePosixPath(raw)
    if not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ThreeMFError(f"unsafe 3MF package member path: {value!r}")
    return candidate.as_posix()


def _relationship_model_member(archive: ZipFile) -> str:
    names = {name.lower(): name for name in archive.namelist()}
    relationships = names.get("_rels/.rels")
    if relationships is not None:
        info = archive.getinfo(relationships)
        if info.file_size > _MAX_RELATIONSHIP_XML_BYTES:
            raise ThreeMFError("3MF package relationships exceed the size limit")
        try:
            relationship_xml = archive.read(relationships)
            upper = relationship_xml.upper()
            if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                raise ThreeMFError(
                    "3MF relationship XML may not contain a DTD or entity declaration"
                )
            root = ElementTree.fromstring(relationship_xml)
        except ElementTree.ParseError as exc:
            raise ThreeMFError(f"invalid 3MF package relationships: {exc}") from exc
        for element in root.iter():
            if _local_name(element.tag) != "Relationship":
                continue
            if element.attrib.get("Type") != _RELATIONSHIP_TYPE:
                continue
            target = element.attrib.get("Target")
            if target:
                member = _package_member(target)
                actual = names.get(member.lower())
                if actual is not None:
                    return actual
                raise ThreeMFError(f"3MF main model member is missing: {member}")
    conventional = names.get("3d/3dmodel.model")
    if conventional is not None:
        return conventional
    models = sorted(name for name in archive.namelist() if name.lower().endswith(".model"))
    if len(models) == 1:
        return models[0]
    raise ThreeMFError("3MF package does not identify exactly one main model")


def _transform(attributes: dict[str, str], unit_scale: float) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    value = attributes.get("transform")
    if value is None:
        return matrix
    try:
        fields = value.split()
        numbers = np.asarray([float(field) for field in fields], dtype=np.float64)
    except ValueError as exc:
        raise ThreeMFError(f"invalid 3MF transform: {value!r}") from exc
    if numbers.shape != (12,) or not np.all(np.isfinite(numbers)):
        raise ThreeMFError("3MF transforms must contain 12 finite numbers")
    matrix[:3, :4] = numbers.reshape((4, 3)).T
    matrix[:3, 3] *= unit_scale
    return matrix


def _mesh_arrays(
    mesh: ElementTree.Element, unit_scale: float, budget: _Budget
) -> tuple[np.ndarray, np.ndarray]:
    vertices_element = next(
        (item for item in mesh if _local_name(item.tag) == "vertices"), None
    )
    triangles_element = next(
        (item for item in mesh if _local_name(item.tag) == "triangles"), None
    )
    if vertices_element is None or triangles_element is None:
        raise ThreeMFError("a 3MF mesh must contain vertices and triangles")

    try:
        vertex_values = [
            f"{item.attrib['x']} {item.attrib['y']} {item.attrib['z']}"
            for item in vertices_element
            if _local_name(item.tag) == "vertex"
        ]
        face_values = [
            f"{item.attrib['v1']} {item.attrib['v2']} {item.attrib['v3']}"
            for item in triangles_element
            if _local_name(item.tag) == "triangle"
        ]
        vertex_flat = np.fromstring(
            " ".join(vertex_values), dtype=np.float64, sep=" "
        )
        face_flat = np.fromstring(" ".join(face_values), dtype=np.int64, sep=" ")
    except (KeyError, ValueError) as exc:
        raise ThreeMFError("3MF mesh contains malformed vertex or triangle data") from exc
    if vertex_flat.size != len(vertex_values) * 3 or face_flat.size != len(face_values) * 3:
        raise ThreeMFError("3MF mesh contains malformed vertex or triangle data")
    budget.source_vertices += len(vertex_values)
    budget.source_faces += len(face_values)
    if budget.source_vertices > _MAX_SOURCE_VERTICES:
        raise ThreeMFError("3MF source exceeds the vertex limit")
    if budget.source_faces > _MAX_SOURCE_FACES:
        raise ThreeMFError("3MF source exceeds the triangle limit")
    vertices = vertex_flat.reshape((-1, 3))
    faces = face_flat.reshape((-1, 3))
    if not len(vertices) or not len(faces):
        raise ThreeMFError("3MF mesh contains no triangle geometry")
    vertices *= unit_scale
    if not np.all(np.isfinite(vertices)):
        raise ThreeMFError("3MF mesh contains non-finite vertex coordinates")
    if np.any(faces < 0) or np.any(faces >= len(vertices)):
        raise ThreeMFError("3MF mesh contains an out-of-range vertex index")
    return vertices, faces


def _path_attribute(attributes: dict[str, str]) -> str | None:
    for name, value in attributes.items():
        if _local_name(name) == "path":
            return value
    return None


def _parse_model(archive: ZipFile, member: str, budget: _Budget) -> _Model:
    info = archive.getinfo(member)
    if info.file_size > _MAX_MODEL_XML_BYTES:
        raise ThreeMFError(f"3MF model XML exceeds the size limit: {member}")
    try:
        stream = archive.open(member)
    except KeyError as exc:
        raise ThreeMFError(f"3MF component model is missing: {member}") from exc
    with stream:
        try:
            tree = ElementTree.parse(_DTDRejectingStream(stream))
        except ElementTree.ParseError as exc:
            raise ThreeMFError(f"invalid XML in 3MF model {member}: {exc}") from exc

    root = tree.getroot()
    unit = root.attrib.get("unit", "millimeter").lower()
    if unit not in _UNIT_TO_MM:
        raise ThreeMFError(f"unsupported 3MF model unit: {unit!r}")
    scale = _UNIT_TO_MM[unit]
    objects: dict[str, _Object] = {}
    build: list[_Component] = []

    for element in root.iter():
        kind = _local_name(element.tag)
        if kind == "object":
            object_id = element.attrib.get("id")
            if not object_id or object_id in objects:
                raise ThreeMFError("3MF objects must have unique non-empty IDs")
            budget.objects += 1
            if budget.objects > _MAX_OBJECTS:
                raise ThreeMFError("3MF source exceeds the object limit")
            vertices: list[np.ndarray] = []
            faces: list[np.ndarray] = []
            components: list[_Component] = []
            for child in element:
                child_kind = _local_name(child.tag)
                if child_kind == "mesh":
                    vertex_array, face_array = _mesh_arrays(child, scale, budget)
                    vertices.append(vertex_array)
                    faces.append(face_array)
                elif child_kind == "components":
                    for component in child:
                        if _local_name(component.tag) != "component":
                            continue
                        target = component.attrib.get("objectid")
                        if not target:
                            raise ThreeMFError("3MF component is missing objectid")
                        budget.components += 1
                        if budget.components > _MAX_COMPONENTS:
                            raise ThreeMFError("3MF source exceeds the component limit")
                        component_path = _path_attribute(component.attrib)
                        target_member = (
                            _package_member(component_path, relative_to=member)
                            if component_path
                            else member
                        )
                        components.append(
                            _Component(
                                target_member,
                                target,
                                _transform(component.attrib, scale),
                            )
                        )
            if not vertices and not components:
                raise ThreeMFError(f"3MF object {object_id} has no mesh or components")
            objects[object_id] = _Object(
                tuple(vertices), tuple(faces), tuple(components)
            )
        elif kind == "build":
            for item in element:
                if _local_name(item.tag) != "item":
                    continue
                target = item.attrib.get("objectid")
                if not target:
                    raise ThreeMFError("3MF build item is missing objectid")
                budget.components += 1
                if budget.components > _MAX_COMPONENTS:
                    raise ThreeMFError("3MF source exceeds the component limit")
                item_path = _path_attribute(item.attrib)
                target_member = (
                    _package_member(item_path, relative_to=member)
                    if item_path
                    else member
                )
                build.append(_Component(target_member, target, _transform(item.attrib, scale)))

    return _Model(objects=objects, build=tuple(build))


def load_3mf_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load built 3MF triangle instances into one deterministic mesh in mm."""

    source = Path(path)
    try:
        archive = ZipFile(source)
    except (OSError, BadZipFile) as exc:
        raise ThreeMFError(f"could not open 3MF package: {exc}") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > _MAX_ARCHIVE_MEMBERS:
            raise ThreeMFError("3MF package exceeds the member-count limit")
        if any(info.flag_bits & 0x1 for info in infos):
            raise ThreeMFError("encrypted 3MF package members are not supported")
        if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
            raise ThreeMFError("3MF package exceeds the uncompressed-size limit")
        for info in infos:
            if info.file_size and info.file_size / max(info.compress_size, 1) > _MAX_COMPRESSION_RATIO:
                raise ThreeMFError(
                    f"3MF package member has a suspicious compression ratio: {info.filename}"
                )
        lowered_names = [info.filename.lower() for info in infos]
        if len(lowered_names) != len(set(lowered_names)):
            raise ThreeMFError("3MF package has ambiguous case-insensitive member names")
        main_member = _relationship_model_member(archive)
        actual_members = {name.lower(): name for name in archive.namelist()}
        models: dict[str, _Model] = {}
        output_vertices: list[np.ndarray] = []
        output_faces: list[np.ndarray] = []
        vertex_count = 0
        budget = _Budget()

        def model_for(member: str) -> tuple[str, _Model]:
            actual = actual_members.get(member.lower())
            if actual is None:
                raise ThreeMFError(f"3MF component model is missing: {member}")
            if actual not in models:
                models[actual] = _parse_model(archive, actual, budget)
            return actual, models[actual]

        def expand(
            reference: _Component,
            parent_transform: np.ndarray,
            active: tuple[tuple[str, str], ...],
        ) -> None:
            nonlocal vertex_count
            member, model = model_for(reference.member)
            key = (member, reference.object_id)
            if key in active:
                raise ThreeMFError("3MF component graph contains a cycle")
            if len(active) >= _MAX_COMPONENT_DEPTH:
                raise ThreeMFError("3MF component graph exceeds the depth limit")
            budget.expanded_instances += 1
            if budget.expanded_instances > _MAX_EXPANDED_INSTANCES:
                raise ThreeMFError("3MF build exceeds the expanded-instance limit")
            object_ = model.objects.get(reference.object_id)
            if object_ is None:
                raise ThreeMFError(
                    f"3MF component references undefined object {reference.object_id!r}"
                )
            world = parent_transform @ reference.transform
            next_active = (*active, key)
            for vertices, faces in zip(object_.vertices, object_.faces, strict=True):
                budget.output_vertices += len(vertices)
                budget.output_faces += len(faces)
                if budget.output_vertices > _MAX_OUTPUT_VERTICES:
                    raise ThreeMFError("3MF build exceeds the expanded-vertex limit")
                if budget.output_faces > _MAX_OUTPUT_FACES:
                    raise ThreeMFError("3MF build exceeds the expanded-triangle limit")
                homogeneous = np.column_stack((vertices, np.ones(len(vertices))))
                transformed = (homogeneous @ world.T)[:, :3]
                output_vertices.append(transformed)
                output_faces.append(faces + vertex_count)
                vertex_count += len(vertices)
            for component in object_.components:
                expand(component, world, next_active)

        main_member, main = model_for(main_member)
        if not main.build:
            raise ThreeMFError(f"3MF main model {main_member} contains no build items")
        identity = np.eye(4, dtype=np.float64)
        for item in main.build:
            expand(item, identity, ())

    if not output_vertices or not output_faces:
        raise ThreeMFError("3MF build contains no triangle geometry")
    vertices = np.vstack(output_vertices)
    faces = np.vstack(output_faces)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def write_3mf_mesh(path: str | Path, mesh: trimesh.Trimesh) -> Path:
    """Write one triangle mesh as a deterministic, face-order-safe 3MF.

    HolderPro uses this narrow writer for support-painted posed references. STL
    import repair is allowed to remove and reorder facets, which would detach a
    face-index paint mask from the model. PrusaSlicer's 3MF loader preserves the
    explicit triangle order, so the paint sidecar and printable pose stay
    registered without adding another runtime dependency.
    """

    output = Path(path)
    if output.suffix.lower() != ".3mf":
        raise ValueError("3MF destination must end in .3mf")
    vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float32)
    faces = np.ascontiguousarray(mesh.faces, dtype=np.int64)
    if (
        vertices.ndim != 2
        or vertices.shape[1:] != (3,)
        or faces.ndim != 2
        or faces.shape[1:] != (3,)
        or not len(vertices)
        or not len(faces)
        or not np.isfinite(vertices).all()
        or np.any(faces < 0)
        or np.any(faces >= len(vertices))
    ):
        raise ThreeMFError("cannot write invalid triangle geometry as 3MF")

    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/model.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(
            output,
            "w",
            compression=ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", relationships)
            with archive.open("3D/model.model", "w", force_zip64=True) as binary:
                with io.TextIOWrapper(binary, encoding="utf-8", newline="\n") as model:
                    model.write(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<model xmlns="http://schemas.microsoft.com/'
                        '3dmanufacturing/core/2015/02" unit="millimeter">\n'
                        "  <resources><object id=\"1\" type=\"model\"><mesh>\n"
                        "    <vertices>\n"
                    )
                    for x, y, z in vertices:
                        model.write(
                            "      <vertex "
                            f'x="{float(x):.9g}" y="{float(y):.9g}" '
                            f'z="{float(z):.9g}"/>\n'
                        )
                    model.write("    </vertices>\n    <triangles>\n")
                    for first, second, third in faces:
                        model.write(
                            "      <triangle "
                            f'v1="{int(first)}" v2="{int(second)}" '
                            f'v3="{int(third)}"/>\n'
                        )
                    model.write(
                        "    </triangles>\n"
                        "  </mesh></object></resources>\n"
                        "  <build><item objectid=\"1\"/></build>\n"
                        "</model>\n"
                    )
    except (OSError, ValueError) as exc:
        raise ThreeMFError(f"could not write posed 3MF reference: {exc}") from exc
    return output


__all__ = ["ThreeMFError", "load_3mf_mesh", "write_3mf_mesh"]
