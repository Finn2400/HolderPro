# User guide

## Paint and pose

Use **Pose object** to change the actual printable orientation. Ordinary drag
adjusts X/Y rotation, Shift-drag adjusts Z rotation, and Alt/Option-drag changes
height. Scrolling in pose mode changes height in one-millimetre steps. Numeric
pose controls remain synchronized.

Use **Orbit** for camera-only movement. Selecting **Paint support**, **Block**,
or **Erase** locks both object pose and camera so a stroke cannot become
misregistered. Switch back to **Orbit** to reposition.

The brush radius is measured in model millimetres. A face is selected when any
part of its triangle lies within the brush sphere, including triangles to the
left and right of the cursor and triangles in separate shells that genuinely
intersect the sphere.

Green paint is a strict contact mask. Once any enforcer paint exists, automatic
overhang contacts are disabled and Organic supports may contact only green
facets. The GUI requires at least one green region.

## Inspect useful surfaces

Use **Under** or **Bottom** to inspect the underside. Blue, gold, and red mark
increasingly down-facing surface angles. Purple emphasizes surfaces that are
both relatively low and locally concave. The yellow center-of-mass marker and
its bed projection help judge stand stability; they do not replace structural
engineering analysis.

## Shape the stand

**Single Organic trunk** is enabled by default. It wraps bed-rooted branches in
a connected footprint and eases that footprint into the genuine Organic
branches over the taper height. Increase taper height for a gentler transition.

**Half-size roots, fuller tips** reduces initial root contribution while
tapering branches more slowly near contact. The connector web remains active so
the slimmer roots still belong to one printable base.

Oversized models warn but continue inside an expanded virtual generation
volume. The exported stand may need splitting or a larger fabrication system.

## Verify before printing

After generation, the cyan support solid appears in the same scene. A pose or
paint change clears it so stale geometry cannot be confused with the current
design. Export only completes after HolderPro reloads the STL and proves a
watertight positive-volume mesh.

Open the output in your slicer, inspect every layer, select material and infill,
and validate stability for the real load. HolderPro's closed geometry does not
force a slicer to use 100% infill.
