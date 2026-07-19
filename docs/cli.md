# Command-line interface

HolderPro installs one command with three subcommands:

```console
holderpro generate --help
holderpro doctor --help
holderpro version
```

## Generate

`holderpro generate INPUT OUTPUT` creates a `GenerationJob` and uses the same
engine and validation path as the desktop application. Run `--help` for the
version-specific pose, contact, branch, and trunk options. Paint interactively
in the desktop application; programmatic callers can populate the documented
paint-face fields on `GenerationJob`.

The destination is replaced atomically only after validation. On failure an
existing output remains untouched. Add `--retain-failed-geometry` only when
you explicitly want model-derived failure artifacts for local debugging; the
default failure path retains no geometry.

## Doctor

`holderpro doctor` checks:

- engine discovery and `--version-json` provenance;
- native executable dependencies;
- temporary and output permissions;
- Qt and VTK availability when the GUI is installed; and
- renderer/OpenGL details when a display is available.

Add `--export PATH` to write a shareable JSON report. Model geometry is
excluded and user paths are redacted by default.

## Version

`holderpro version` prints application and engine identity. Use `--json` for
machine-readable output in bug reports and release validation. It reports
engine provenance but deliberately omits the local executable path.
