# PrusaSlicer Organic Supports: auditable source snapshot

This directory is a byte-for-byte, **unmodified** snapshot of the source files
that define and expose PrusaSlicer's Organic Supports pipeline. It is pinned to
the current stable PrusaSlicer release used as HolderPro's exact-engine
baseline:

- repository: `https://github.com/prusa3d/PrusaSlicer.git`
- tag: `version_2.9.6`
- peeled tag commit: `b028299c770b8380ee81c921a2867d522f288123`
- release date: 2026-06-25
- license: GNU AGPL v3 or later (see `LICENSE`)

HolderPro's native adapter builds against a complete checkout at that exact
commit. This small, byte-identical subset exists only to make the Organic
implementation and export boundary easy to audit; it is not a buildable copy
and is not the complete Corresponding Source attached to binary releases.
`MANIFEST.sha256` makes the unmodified boundary auditable.

## Why this is the real implementation

HolderPro ships only this exact upstream Organic implementation. The retired
independent approximation is archived separately and is never used as a
fallback by the current product.

The canonical PrusaSlicer call chain is:

```text
PrintObject::_generate_support_material
  -> fff_tree_support_generate
  -> generate_support_areas
     -> generate_overhangs
     -> TreeModelVolumes::precalculate
     -> generate_initial_areas
     -> create_layer_pathing
     -> create_nodes_from_area
     -> organic_draw_branches
        -> organic_smooth_branches_avoid_collisions
        -> extrude_branch
        -> slice_mesh
        -> union filled support polygons
     -> generate_interface_layers
     -> generate_support_layers
     -> generate_support_toolpaths  # hollow printing policy begins here
```

The important finding is that Organic Supports are not hollow in the geometry
solver. `OrganicSupport.cpp::extrude_branch` creates capped triangle meshes,
and `organic_draw_branches` slices them into filled polygon footprints. The
later toolpath stage intentionally replaces those filled areas with perimeter
walls. The upstream comment immediately before that step says:

```text
Don't fill in the tree supports, make them hollow with just a single sheath line.
```

That means a supports-only tool does not need a new support algorithm. It needs
a narrow output adapter at the filled-layer boundary, before the hollow
toolpath policy.

## Isolated modules

### Organic routing and geometry

- `Support/TreeSupport.cpp`, `.hpp`: overhang detection, tip placement,
  downward influence-area propagation, merging, node placement, orchestration.
- `Support/OrganicSupport.cpp`, `.hpp`: Organic smoothing, collision nudging,
  capped branch meshing, slicing, clipping, and polygon union.
- `Support/TreeModelVolumes.cpp`, `.hpp`: collision, avoidance, wall
  restriction, and placeable-area caches.
- `Support/TreeSupportCommon.cpp`, `.hpp`: settings mapping, branch radii, and
  allowed movement per layer.

### Contacts, layers, and the hollowing boundary

- `Support/SupportCommon.cpp`, `.hpp`: contacts, interfaces, final support
  layers, and Organic perimeter-only toolpaths.
- `Support/SupportLayer.hpp`: filled intermediate polygon storage.
- `Support/SupportParameters.cpp`, `.hpp`: support/interface parameters.

### Integration and solid export seam

- `PrintObject.cpp`: dispatch into the tree/Organic generator.
- `Print.hpp`: access to `PrintObject::support_layers()`.
- `Layer.hpp`: `SupportLayer::support_islands`, which retains filled support
  footprints separately from extrusion paths.
- `PrintConfig.cpp`: Organic settings and defaults.
- `SlicesToTriangleMesh.cpp`, `.hpp`: upstream filled-slice-to-closed-mesh
  utility available to a later adapter.
- `CMakeLists.txt`: proves these modules belong to the full `libslic3r` target.

## Standalone boundary

These files are isolated for audit, not presented as a separately compilable
mini-library. Prusa's tree solver depends deeply on polygon operations,
slicing, configuration, TBB, Eigen, and other `libslic3r` types. Copying a few
`.cpp` files into the Python application would create a fragile fork.

The exact supports-only architecture is therefore:

```text
HolderPro GUI / CLI / Python API
  -> thin native command-line adapter
  -> unmodified, pinned libslic3r Organic pipeline
  -> filled support_islands for each layer
  -> closed solid mesh conversion
  -> support-only STL or 3MF
```

The adapter lives in `native/`. It exposes model placement and existing
Organic settings, runs the normal pipeline, and exports the filled support
layers. It does not reconstruct shapes from G-code or fall back to an
independent support generator.

## Verify the snapshot

From this directory:

```bash
shasum -a 256 -c MANIFEST.sha256
```

All paths in the manifest are relative to this directory.

## Licensing boundary

PrusaSlicer and these copied files are AGPL-3.0-or-later and retain their
upstream license and notices. The complete HolderPro shipping repository is
also AGPL-3.0-or-later. Every HolderPro binary release is accompanied by a
separate Corresponding Source archive containing the full pinned PrusaSlicer
source, the adapter, all HolderPro code, and the build and packaging material
needed for that exact release.
