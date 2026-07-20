# Changelog

All notable changes to HolderPro are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Packaging and release infrastructure for the exact Prusa-based Organic tool.
- Cross-platform native-engine and platform-wheel release matrices.
- Corresponding-source, SBOM, provenance, checksum, attestation, and
  clean-install gates.
- Public `holderpro` command structure and `holderpro-gui` launcher.
- Fail-closed macOS deployment-target audits.
- Permanent free-software governance, release-authenticity, privacy, and
  uninstalling policies.
- A legal-review brief and exact GitHub Releases and PyPI setup guide.
- Visible world-axis pose rings with hover/drag feedback, model-direct
  camera-relative tumbling, and background-only camera orbiting.
- A labelled build plate with distinct 10 mm, 50 mm, and perimeter lines that
  automatically switch between white and black for background contrast.

### Changed

- HolderPro now refers exclusively to the exact Organic-support product.
- The shipping project is AGPL-3.0-or-later.
- Painted posed meshes now use a dependency-free, face-order-preserving 3MF
  handoff so PrusaSlicer repair cannot detach paint from triangle indices.
- The complete Python package is covered by the mypy release gate.
- The official distribution is limited to the same four bundled-engine wheels
  on GitHub Releases and PyPI; GUI dependencies remain separately installable.
- STL preview vertices are welded without changing source faces, retaining full
  reference detail while making dense-model posing responsive and restoring
  adjacency-based concavity highlighting.

### Fixed

- Single-trunk jobs now reject disconnected geometry before atomic replacement
  and recheck connectivity after STL serialization.
- Printable tangent-shell STL fallbacks now remain visible in the VTK preview.
- Output paths, painted indices, diagnostic path privacy, cancellation, long
  paths, and read-only installations fail safely or are regression-tested.
- Native support-layer staging now creates an atomically private directory and
  reserves its output exclusively, using `0700`/`0600` modes on POSIX and a
  protected owner-only DACL on Windows.
- Native dependency builds tolerate CMake 4's removed legacy-policy default,
  prefetch GMP from hash-verified GNU mirrors, and provision Linux OpenGL
  development headers explicitly.
- Automated PrusaSlicer checkouts disable host line-ending conversion so the
  reviewed source hashes remain identical on Windows, macOS, and Linux.
- The Prusa dependency driver now runs one top-level ExternalProject at a time;
  each project retains its own parallel build without nested oversubscription.
- Linux native builders install the upstream configure-time DBus metadata
  explicitly; the headless engine neither links nor ships DBus.
- The MSVC adapter target now receives PrusaSlicer's Windows math and Boost
  header definitions explicitly instead of relying on upstream directory scope.
- PyPI publication grants OIDC only to the two-action publish job; source and
  artifact checks run before that credential is available.
- Protected-main tooling now validates annotated release tags before tagged
  code executes, and partial PyPI uploads resume only after exact wheel hashes
  are verified.
- Dense preview inspection now reports the displayed face safely, and pose
  heatmaps update once at drag/scroll completion instead of blocking each move.
- macOS clean-wheel GUI release tests now use Qt's native Cocoa platform,
  avoiding the QVTK crash caused by Qt's generic offscreen platform plugin.

### Removed

- The retired independent approximation and its legacy dependency stack.
- Standalone DMGs, Windows installers, portable application archives,
  AppImages, PyInstaller freezing, and paid platform-signing services.

[Unreleased]: https://github.com/Finn2400/HolderPro/compare/HEAD...HEAD
