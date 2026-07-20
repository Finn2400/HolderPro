# Architecture

HolderPro has one generation pipeline shared by the Python API, CLI, and GUI.

```text
GUI / CLI / Python caller
          |
          v
     GenerationJob
          |
          +-- pose + face-order-safe 3MF + painted-facet sidecar
          |
          v
holderpro-organic-engine
  pinned PrusaSlicer Print::process()
  unmodified Organic implementation
          |
          v
holderpro.organic-support-layers/v1
          |
          v
connected-trunk layer operations
          |
          v
manifold solidification and STL validation
          |
          v
atomic support-only STL + GenerationResult
```

## Boundaries

### Public Python surface

`GenerationJob`, `GenerationResult`, `EngineInfo`, `generate()`, and the
documented error hierarchy are the supported API. The CLI and GUI may not
duplicate job validation or import each other's private implementation.

### Native adapter

`holderpro-organic-engine` is a static headless executable, not a stable shared
library. PrusaSlicer exposes no supported `libslic3r` ABI. The adapter runs the
normal `Print::process()` path and captures `SupportLayer::support_islands`
before toolpath hollowing. It does not use the stock PrusaSlicer CLI because
that CLI cannot export this filled intermediate geometry.

The native interface is versioned through `--version-json`, which reports the
HolderPro and adapter versions, PrusaSlicer version and commit, layer schema,
paint schema, OS, architecture, and build identifier.

### Layer document

`holderpro.organic-support-layers/v1` is intentionally retained for existing
diagnostics and regression fixtures. Schema changes require a new identifier,
reader compatibility policy, fixtures, and release note.

### Geometry contract

- 3MF Core/Production geometry is loaded by HolderPro's bounded stdlib/NumPy
  reader, so core installs do not acquire trimesh's optional NetworkX/lxml
  dependency path.
- Green paint is a strict allow-list for support contact.
- Paint indices are fingerprinted, remapped when float32 coordinates collapse
  triangles, and handed to the engine with a minimal face-order-preserving 3MF.
  This avoids PrusaSlicer's normal STL repair removing or reordering painted
  facets.
- Pose changes invalidate paint registration and stale previews.
- Oversized models expand the virtual generation volume after warning.
- Enabled single-trunk results must be one connected printable base.
- Export replaces an existing destination only after the exact serialized STL
  proves positive volume, required single-trunk connectivity, and either a
  strict topology reload or acceptance by the pinned PrusaSlicer importer.
- Native intermediate output is staged in an atomically private directory and
  exclusively reserved before writing, then moved into place atomically.

### GUI boundary

VTK remains an optional GUI dependency for the v1 renderer, picking, face
colors, camera, center-of-mass overlay, and support display. Brush selection
uses `vtkStaticCellLocator` followed by exact triangle-to-sphere distance tests.
Preview reduction uses VTK's quadric decimator. Replacing VTK with a custom
`QOpenGLWidget` is a post-v1 size optimization, not part of the initial release.

## Distribution

Each platform wheel includes one matching native engine. GitHub Releases carry
the four exact wheels and their source/provenance material; PyPI receives those
same wheels without rebuilding. The optional GUI remains an ordinary Python
extra, so Qt and VTK are installed as separate, replaceable distributions
rather than copied into a frozen application.

The native engine never downloads a component at runtime. User models, paint,
temporary geometry, and diagnostic bundles remain local.
