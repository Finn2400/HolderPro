# Troubleshooting

Start with:

```console
holderpro doctor
holderpro version --json
```

## Engine not found or rejected

Official wheels and desktop packages contain the matching
`holderpro-organic-engine`. Reinstall the package for your platform; do not
download an engine from an unrelated release. `doctor` reports the searched
locations and rejects a version/commit mismatch.

## No support appears

The GUI uses a strict paint mask. Confirm that at least one face is green, that
the painted area is actually down-facing in the current pose, and that contact
settings allow a path to the bed. Selecting a brush locks the pose; stale paint
cannot silently follow a later transformation.

## The object is larger than the plate

This is a warning. HolderPro expands its virtual generation volume and creates
the full stand. You are responsible for splitting it or choosing equipment that
can manufacture it.

## STL validation failed

HolderPro fails closed when STL float32 encoding cannot preserve a watertight
positive-volume solid. By default it retains no failure geometry. If you have
permission to keep model-derived data, reproduce from the CLI with
`--retain-failed-geometry`; HolderPro writes mode-0600 artifacts into a unique
mode-0700 temporary directory and reports that path. A diagnostic JSON export
still excludes geometry. Do not share a user model or retained artifacts
without permission.

## Blank or corrupted viewer

Update the graphics driver on Windows/Linux, avoid remote-desktop OpenGL
translation when possible, and include `doctor`'s Qt/VTK/OpenGL report. The
viewer is not the exported STL; validate the file separately in a slicer.

## Reporting

Use the bug template and attach a redacted diagnostic bundle plus a synthetic
reproducer. See [SUPPORT.md](../SUPPORT.md).
