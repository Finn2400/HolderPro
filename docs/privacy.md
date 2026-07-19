# Privacy and network behavior

HolderPro does not include telemetry, analytics, crash uploading, or automatic
updates. Generation runs locally. A manual **Check for updates** action may open
the HolderPro GitHub Releases page in the user's browser.

Diagnostic bundles exclude model geometry and redact home directories, user
names, temporary paths, and output paths by default. The user must explicitly
choose to create and share a bundle. Failed geometry is not retained by
default. The CLI's explicit `--retain-failed-geometry` option writes private
artifacts into a unique restricted temporary directory because those files may
reveal model-derived shapes. `holderpro version --json` also omits the native
engine's local installation path so its normal bug-report output is safe to
paste publicly.

Installing from PyPI or downloading a GitHub release necessarily contacts those
services outside HolderPro itself. Operating systems may also perform their own
signature, notarization, or reputation checks.
