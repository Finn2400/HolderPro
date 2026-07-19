# Changelog

All notable changes to HolderPro are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Packaging and release infrastructure for the exact Prusa-based Organic tool.
- Cross-platform native, wheel, and desktop release matrices.
- Corresponding-source, SBOM, provenance, signature, and clean-install gates.
- Public `holderpro` command structure and `holderpro-gui` launcher.

### Changed

- HolderPro now refers exclusively to the exact Organic-support product.
- The shipping project is AGPL-3.0-or-later.
- Painted posed meshes now use a dependency-free, face-order-preserving 3MF
  handoff so PrusaSlicer repair cannot detach paint from triangle indices.
- The complete Python package is covered by the mypy release gate.

### Fixed

- Single-trunk jobs now reject disconnected geometry before atomic replacement
  and recheck connectivity after STL serialization.
- Printable tangent-shell STL fallbacks now remain visible in the VTK preview.
- Output paths, painted indices, diagnostic path privacy, cancellation, long
  paths, and read-only installations fail safely or are regression-tested.
- Native support-layer staging now creates an atomically private directory and
  reserves its output exclusively, using `0700`/`0600` modes on POSIX and a
  protected owner-only DACL on Windows.

### Removed

- The retired independent approximation and its legacy dependency stack.

[Unreleased]: https://github.com/Finn2400/HolderPro/compare/HEAD...HEAD
