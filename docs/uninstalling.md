# Uninstalling HolderPro

HolderPro is installed from PyPI into a Python environment. Remove it with the
same Python interpreter that installed it:

```console
python -m pip uninstall holderpro
```

The optional GUI dependencies are separate packages and are not removed
automatically. Remove them only if no other installed application needs them:

```console
python -m pip uninstall PySide6-Essentials shiboken6 vtk
```

Uninstalling HolderPro never deletes reference models, generated STL files, or
diagnostic exports.

The GUI stores recent-file paths, window layout, generation settings, and
first-run state using Qt's per-user application settings. These small settings
remain after `pip uninstall` so a later installation can reuse them. They can
be removed through the operating system's normal Qt application-settings
location for an application named `HolderPro`; inspect the stored data before
deleting it if other HolderPro installations share the same user account.
