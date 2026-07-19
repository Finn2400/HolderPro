"""HolderPro desktop interface for painting and generating Organic supports."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .diagnostics import DoctorReport
    from .runner import GenerationJob

try:  # Permit headless imports (for example, command-line and packaging tools).
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover - depends on the local environment
    QtCore = QtGui = QtWidgets = None  # type: ignore[assignment]
    _PYSIDE_IMPORT_ERROR: ImportError | None = exc
else:
    _PYSIDE_IMPORT_ERROR = None

from .workers import DiagnosticsWorker, GenerateFunction, GenerationWorker

_STYLE = """
QMainWindow { background: #191c20; }
QWidget { color: #edf1f4; background-color: #191c20; font-size: 12px; }
QLabel#title { font-size: 20px; font-weight: 700; }
QLabel#muted { color: #aeb6bf; }
QLabel#status { padding: 8px; background: #15181b; border-radius: 5px; }
QGroupBox { border: 1px solid #3b434c; border-radius: 6px; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QLineEdit, QDoubleSpinBox, QSpinBox {
  background: #15181b; border: 1px solid #414952; border-radius: 4px; padding: 5px;
  selection-background-color: #315f7c; selection-color: #ffffff;
}
QPushButton {
  padding: 7px 10px; border-radius: 5px; background: #343a42;
  border: 1px solid #4a525d;
}
QPushButton:hover { background: #3d4650; }
QPushButton:checked { background: #235f7f; border-color: #53a5d2; }
QPushButton:disabled { color: #747b83; background: #292d32; border-color: #353b42; }
QPushButton#generate { background: #246f9f; border-color: #4394c6; font-weight: 700; }
QPushButton#generate:hover { background: #2b7bac; }
QPushButton#cancel { background: #5c3434; border-color: #8d5555; }
QProgressBar { background: #15181b; border: 1px solid #3d444d; border-radius: 4px; }
QProgressBar::chunk { background: #3d8fbe; }
"""

_SOURCE_URL = "https://github.com/Finn2400/HolderPro"
_RELEASES_URL = f"{_SOURCE_URL}/releases"
_SUPPORTED_MODEL_SUFFIXES = frozenset({".stl", ".3mf", ".obj"})
_MAX_RECENT_FILES = 8


def _application_icon_path() -> Path:
    return Path(__file__).with_name("assets") / "holderpro.svg"


def _release_tag(version: str) -> str:
    prerelease = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", version)
    if prerelease is not None:
        label = {"a": "alpha", "b": "beta", "rc": "rc"}[prerelease.group(2)]
        return f"v{prerelease.group(1)}-{label}.{prerelease.group(3)}"
    return f"v{version}"


def require_pyside6() -> None:
    """Raise an actionable error if PySide6 is not installed."""

    if QtWidgets is None:
        raise RuntimeError(
            "HolderPro's desktop UI is not installed. Run "
            '`python -m pip install "holderpro[gui]"` and try again.'
        ) from _PYSIDE_IMPORT_ERROR


if QtWidgets is not None:
    from .preview import (
        PAINT_MODE_BLOCKER,
        PAINT_MODE_ENFORCER,
        PAINT_MODE_ERASE,
        PAINT_MODE_INSPECT,
        PAINT_MODE_POSE,
        ModelPreviewWidget,
        load_support_preview_mesh,
    )

    def _number_field(
        minimum: float,
        maximum: float,
        value: float,
        *,
        step: float,
        suffix: str,
        decimals: int = 2,
    ) -> Any:
        field = QtWidgets.QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        field.setSingleStep(step)
        field.setDecimals(decimals)
        field.setSuffix(suffix)
        field.setKeyboardTracking(False)
        return field

    class OrganicSupportsWindow(QtWidgets.QMainWindow):
        """A focused form that exports filled, supports-only STL geometry."""

        generationCompleted = QtCore.Signal(object)

        def __init__(
            self,
            initial_path: str | Path | None = None,
            *,
            output_path: str | Path | None = None,
            generate_fn: GenerateFunction | None = None,
        ) -> None:
            super().__init__()
            self._generate_fn = generate_fn
            self._thread: Any | None = None
            self._worker: GenerationWorker | None = None
            self._diagnostics_thread: Any | None = None
            self._diagnostics_worker: DiagnosticsWorker | None = None
            self._diagnostics_show_dialog = False
            self._diagnostics_first_run = False
            self._pending_diagnostics_export: Path | None = None
            self._active_output: Path | None = None
            self._close_when_idle = False
            self._output_was_edited = output_path is not None
            self._painting_locked = False
            self.preview: ModelPreviewWidget
            self.brush_radius_spin: Any
            self.low_height_spin: Any
            self._settings = QtCore.QSettings("HolderPro", "HolderPro")
            recent_value = self._settings.value("files/recent", [], list)
            recent = recent_value if isinstance(recent_value, list) else []
            self._recent_files = [str(value) for value in recent][:_MAX_RECENT_FILES]

            self.setWindowTitle("HolderPro")
            self.setMinimumSize(1100, 720)
            self.resize(1460, 900)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.setAcceptDrops(True)
            icon_path = _application_icon_path()
            if icon_path.is_file():
                self.setWindowIcon(QtGui.QIcon(str(icon_path)))
            self.setStyleSheet(_STYLE)
            self._build_ui()
            self._build_menus()
            self._restore_settings()

            if initial_path is not None:
                self.set_input_path(initial_path)
            if output_path is not None:
                self.output_path_edit.setText(str(Path(output_path).expanduser()))
            if self._generate_fn is None and not self._settings.value(
                "diagnostics/firstRunComplete", False, bool
            ):
                QtCore.QTimer.singleShot(0, self._run_first_diagnostics)

        # ---- Form construction ---------------------------------------------

        def _build_ui(self) -> None:
            contents = QtWidgets.QWidget()
            self.setCentralWidget(contents)
            layout = QtWidgets.QVBoxLayout(contents)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(11)

            title = QtWidgets.QLabel("Organic Supports Only")
            title.setObjectName("title")
            layout.addWidget(title)

            subtitle = QtWidgets.QLabel(
                "Inspect underside angle and low concave pockets, then paint real "
                "PrusaSlicer support enforcers or blockers directly on the model."
            )
            subtitle.setObjectName("muted")
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)

            splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
            splitter.setChildrenCollapsible(False)
            splitter.addWidget(self._build_preview_panel())

            form_scroll = QtWidgets.QScrollArea()
            form_scroll.setWidgetResizable(True)
            form_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            form_scroll.setMinimumWidth(390)
            form_scroll.setMaximumWidth(510)
            form_contents = QtWidgets.QWidget()
            form_scroll.setWidget(form_contents)
            form_layout = QtWidgets.QVBoxLayout(form_contents)
            form_layout.setContentsMargins(4, 0, 4, 0)
            form_layout.setSpacing(10)
            form_layout.addWidget(self._build_files_group())
            form_layout.addWidget(self._build_pose_group())
            form_layout.addWidget(self._build_settings_group())
            form_layout.addWidget(self._build_base_group())
            form_layout.addStretch(1)
            splitter.addWidget(form_scroll)
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 0)
            splitter.setSizes((960, 430))
            layout.addWidget(splitter, 1)

            for field in (
                self.bottom_height_spin,
                self.rotation_x_spin,
                self.rotation_y_spin,
                self.rotation_z_spin,
            ):
                field.valueChanged.connect(self._update_preview_pose)
            for field in (
                self.layer_height_spin,
                self.branch_diameter_spin,
                self.branch_diameter_angle_spin,
                self.tip_diameter_spin,
                self.branch_angle_spin,
                self.branch_angle_slow_spin,
                self.contact_distance_spin,
                self.base_thickness_spin,
                self.base_beam_width_spin,
                self.base_node_diameter_spin,
            ):
                field.valueChanged.connect(self._invalidate_generated_support)
            self.network_base_checkbox.toggled.connect(
                self._invalidate_generated_support
            )
            self.output_path_edit.textEdited.connect(
                self._invalidate_generated_support
            )

            self.progress_bar = QtWidgets.QProgressBar()
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(False)
            layout.addWidget(self.progress_bar)

            self.status_label = QtWidgets.QLabel(
                "Choose a model and output file to begin."
            )
            self.status_label.setObjectName("status")
            self.status_label.setWordWrap(True)
            self.status_label.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(self.status_label)

            actions = QtWidgets.QHBoxLayout()
            self.generate_button = QtWidgets.QPushButton("Generate supports")
            self.generate_button.setObjectName("generate")
            self.generate_button.setDefault(True)
            self.generate_button.clicked.connect(self._start_generation)
            actions.addWidget(self.generate_button, 1)

            self.cancel_button = QtWidgets.QPushButton("Cancel")
            self.cancel_button.setObjectName("cancel")
            self.cancel_button.setEnabled(False)
            self.cancel_button.clicked.connect(self._cancel_generation)
            actions.addWidget(self.cancel_button)
            layout.addLayout(actions)

        def _build_menus(self) -> None:
            file_menu = self.menuBar().addMenu("&File")
            self.open_action = QtGui.QAction("&Open model…", self)
            self.open_action.setShortcut(QtGui.QKeySequence.StandardKey.Open)
            self.open_action.triggered.connect(self._choose_input)
            file_menu.addAction(self.open_action)

            self.recent_menu = file_menu.addMenu("Open &Recent")
            self._recent_actions: list[Any] = []
            for _index in range(_MAX_RECENT_FILES):
                action = QtGui.QAction(self)
                action.setVisible(False)
                action.triggered.connect(
                    lambda _checked=False, item=action: self._open_recent(
                        str(item.data())
                    )
                )
                self.recent_menu.addAction(action)
                self._recent_actions.append(action)
            self.recent_menu.addSeparator()
            self.clear_recent_action = QtGui.QAction("Clear Recent Files", self)
            self.clear_recent_action.triggered.connect(self._clear_recent_files)
            self.recent_menu.addAction(self.clear_recent_action)
            self._refresh_recent_menu()

            file_menu.addSeparator()
            self.generate_action = QtGui.QAction("&Generate supports", self)
            self.generate_action.setShortcut(QtGui.QKeySequence("Ctrl+G"))
            self.generate_action.triggered.connect(self._start_generation)
            file_menu.addAction(self.generate_action)
            self.export_diagnostics_action = QtGui.QAction("Export &diagnostics…", self)
            self.export_diagnostics_action.setShortcut(
                QtGui.QKeySequence("Ctrl+Shift+D")
            )
            self.export_diagnostics_action.triggered.connect(self._export_diagnostics)
            file_menu.addAction(self.export_diagnostics_action)
            file_menu.addSeparator()
            quit_action = QtGui.QAction("&Quit HolderPro", self)
            quit_action.setShortcut(QtGui.QKeySequence.StandardKey.Quit)
            quit_action.triggered.connect(self.close)
            file_menu.addAction(quit_action)

            edit_menu = self.menuBar().addMenu("&Edit")
            self._mode_actions: list[Any] = []
            for index, (label, shortcut) in enumerate(
                (
                    ("Orbit / inspect", "1"),
                    ("Pose object", "2"),
                    ("Paint support", "3"),
                    ("Paint blocker", "4"),
                    ("Erase paint", "5"),
                )
            ):
                action = QtGui.QAction(label, self)
                action.setShortcut(QtGui.QKeySequence(shortcut))
                action.triggered.connect(
                    lambda _checked=False, selected=index: self.paint_buttons[
                        selected
                    ].click()
                )
                edit_menu.addAction(action)
                self._mode_actions.append(action)
            edit_menu.addSeparator()
            self.clear_paint_action = QtGui.QAction("Clear support paint", self)
            self.clear_paint_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+X"))
            self.clear_paint_action.triggered.connect(self.preview.clear_paint)
            edit_menu.addAction(self.clear_paint_action)

            view_menu = self.menuBar().addMenu("&View")
            self._view_actions: list[Any] = []
            for label, shortcut, callback in (
                ("Underside isometric", "U", self.preview.view_under_isometric),
                ("Bottom", "B", self.preview.view_bottom),
                ("Isometric", "I", self.preview.view_isometric),
                ("Fit model and supports", "F", self.preview.fit_camera),
            ):
                action = QtGui.QAction(label, self)
                action.setShortcut(QtGui.QKeySequence(shortcut))
                action.triggered.connect(callback)
                view_menu.addAction(action)
                self._view_actions.append(action)

            help_menu = self.menuBar().addMenu("&Help")
            diagnostics_action = QtGui.QAction("Run diagnostics", self)
            diagnostics_action.triggered.connect(self._show_diagnostics)
            help_menu.addAction(diagnostics_action)
            update_action = QtGui.QAction("Check for updates…", self)
            update_action.triggered.connect(
                lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(_RELEASES_URL))
            )
            help_menu.addAction(update_action)
            help_menu.addSeparator()
            about_action = QtGui.QAction("About / Legal Notices", self)
            about_action.triggered.connect(self._show_about)
            help_menu.addAction(about_action)

        def _restore_settings(self) -> None:
            geometry = self._settings.value("window/geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)
            fields = {
                "bottomHeight": self.bottom_height_spin,
                "rotationX": self.rotation_x_spin,
                "rotationY": self.rotation_y_spin,
                "rotationZ": self.rotation_z_spin,
                "layerHeight": self.layer_height_spin,
                "branchDiameter": self.branch_diameter_spin,
                "branchDiameterAngle": self.branch_diameter_angle_spin,
                "tipDiameter": self.tip_diameter_spin,
                "branchAngle": self.branch_angle_spin,
                "branchAngleSlow": self.branch_angle_slow_spin,
                "contactDistance": self.contact_distance_spin,
                "baseThickness": self.base_thickness_spin,
                "baseBeamWidth": self.base_beam_width_spin,
                "baseNodeDiameter": self.base_node_diameter_spin,
                "brushRadius": self.brush_radius_spin,
            }
            for name, field in fields.items():
                value = self._settings.value(f"generation/{name}")
                if value is not None:
                    try:
                        field.setValue(float(value))
                    except (TypeError, ValueError):
                        pass
            low_height = self._settings.value("view/lowHeightPercent")
            if low_height is not None:
                try:
                    self.low_height_spin.setValue(int(low_height))
                except (TypeError, ValueError):
                    pass
            self.network_base_checkbox.setChecked(
                bool(self._settings.value("generation/singleTrunk", True, bool))
            )
            self.slim_full_tip_checkbox.setChecked(
                bool(self._settings.value("generation/slimFullTip", False, bool))
            )

        def _save_settings(self) -> None:
            self._settings.setValue("window/geometry", self.saveGeometry())
            fields = {
                "bottomHeight": self.bottom_height_spin,
                "rotationX": self.rotation_x_spin,
                "rotationY": self.rotation_y_spin,
                "rotationZ": self.rotation_z_spin,
                "layerHeight": self.layer_height_spin,
                "branchDiameter": self.branch_diameter_spin,
                "branchDiameterAngle": self.branch_diameter_angle_spin,
                "tipDiameter": self.tip_diameter_spin,
                "branchAngle": self.branch_angle_spin,
                "branchAngleSlow": self.branch_angle_slow_spin,
                "contactDistance": self.contact_distance_spin,
                "baseThickness": self.base_thickness_spin,
                "baseBeamWidth": self.base_beam_width_spin,
                "baseNodeDiameter": self.base_node_diameter_spin,
                "brushRadius": self.brush_radius_spin,
            }
            for name, field in fields.items():
                self._settings.setValue(f"generation/{name}", field.value())
            self._settings.setValue(
                "generation/singleTrunk", self.network_base_checkbox.isChecked()
            )
            self._settings.setValue(
                "generation/slimFullTip", self.slim_full_tip_checkbox.isChecked()
            )
            self._settings.setValue(
                "view/lowHeightPercent", self.low_height_spin.value()
            )
            self._settings.setValue("files/recent", self._recent_files)
            self._settings.sync()

        def _build_preview_panel(self) -> Any:
            panel = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 8, 0)
            layout.setSpacing(7)

            tools = QtWidgets.QHBoxLayout()
            tools.setSpacing(5)
            self.paint_buttons: list[Any] = []
            modes = (
                (
                    "Orbit",
                    PAINT_MODE_INSPECT,
                    "Rotate the view with the left mouse button.",
                ),
                (
                    "Pose object",
                    PAINT_MODE_POSE,
                    "Drag to rotate; Shift-drag rotates around Z; Option/Alt-drag "
                    "or scroll raises and lowers the model.",
                ),
                (
                    "Paint support",
                    PAINT_MODE_ENFORCER,
                    "Green faces enforce Organic support.",
                ),
                ("Block", PAINT_MODE_BLOCKER, "Red faces block Organic support."),
                ("Erase", PAINT_MODE_ERASE, "Remove painted state with the brush."),
            )
            for index, (label, mode, tooltip) in enumerate(modes):
                button = QtWidgets.QPushButton(label)
                button.setCheckable(True)
                button.setToolTip(tooltip)
                button.clicked.connect(
                    lambda _checked=False, selected=mode: self.preview.set_paint_mode(
                        selected
                    )
                )
                tools.addWidget(button)
                self.paint_buttons.append(button)
                if index == 0:
                    button.setChecked(True)
            paint_group = QtWidgets.QButtonGroup(panel)
            paint_group.setExclusive(True)
            for button in self.paint_buttons:
                paint_group.addButton(button)
            self._paint_button_group = paint_group

            tools.addSpacing(8)
            tools.addWidget(QtWidgets.QLabel("Brush"))
            self.brush_radius_spin = _number_field(
                0.1, 50.0, 3.0, step=0.5, suffix=" mm", decimals=1
            )
            self.brush_radius_spin.setMaximumWidth(100)
            self.brush_radius_spin.valueChanged.connect(self._on_brush_radius_changed)
            tools.addWidget(self.brush_radius_spin)
            self.paint_lock_label = QtWidgets.QLabel("🔒 Position locked")
            self.paint_lock_label.setStyleSheet("color:#70d99a; font-weight:700;")
            self.paint_lock_label.setToolTip(
                "Orbit, pan, zoom, model rotation, and height are locked "
                "while painting."
            )
            self.paint_lock_label.setVisible(False)
            tools.addWidget(self.paint_lock_label)
            tools.addStretch(1)
            self.view_buttons: list[Any] = []
            for label, callback in (
                ("Under", lambda: self.preview.view_under_isometric()),
                ("Bottom", lambda: self.preview.view_bottom()),
                ("Iso", lambda: self.preview.view_isometric()),
                ("Fit", lambda: self.preview.fit_camera()),
            ):
                button = QtWidgets.QPushButton(label)
                button.clicked.connect(callback)
                tools.addWidget(button)
                self.view_buttons.append(button)
            layout.addLayout(tools)

            self.preview = ModelPreviewWidget(panel)
            self.preview.paintChanged.connect(self._on_paint_changed)
            self.preview.paintModeChanged.connect(self._on_paint_mode_changed)
            self.preview.interactionModeChanged.connect(
                self._on_interaction_mode_changed
            )
            self.preview.poseEdited.connect(self._on_preview_pose_edited)
            self.preview.surfacePicked.connect(self._on_surface_picked)
            layout.addWidget(self.preview, 1)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Low-pocket highlight below"))
            self.low_height_spin = QtWidgets.QSpinBox()
            self.low_height_spin.setRange(5, 100)
            self.low_height_spin.setValue(35)
            self.low_height_spin.setSuffix("% height")
            self.low_height_spin.valueChanged.connect(
                lambda value: self.preview.set_low_height_fraction(value / 100.0)
            )
            controls.addWidget(self.low_height_spin)
            controls.addSpacing(10)
            self.paint_count_label = QtWidgets.QLabel("Green 0  •  blocked 0")
            self.paint_count_label.setObjectName("muted")
            controls.addWidget(self.paint_count_label)
            controls.addStretch(1)
            self.surface_info_label = QtWidgets.QLabel(
                "Click a face to read its underside angle and relative height."
            )
            self.surface_info_label.setObjectName("muted")
            controls.addWidget(self.surface_info_label)
            clear_button = QtWidgets.QPushButton("Clear paint")
            clear_button.clicked.connect(self.preview.clear_paint)
            controls.addWidget(clear_button)
            self.clear_paint_button = clear_button
            layout.addLayout(controls)

            legend = QtWidgets.QHBoxLayout()
            legend.setSpacing(10)
            for color, text in (
                ("#434b54", "top / side"),
                ("#2d8bbe", "shallow underside"),
                ("#f6be37", "angled underside"),
                ("#eb463a", "down-facing"),
                ("#d23ee2", "low concave pocket"),
                ("#35dc69", "support paint"),
                ("#ef4853", "blocked"),
                ("#2ec7d6", "generated supports"),
                ("#ffe029", "center of mass"),
            ):
                legend.addWidget(self._legend_item(color, text))
            legend.addStretch(1)
            layout.addLayout(legend)
            return panel

        @staticmethod
        def _legend_item(color: str, text: str) -> Any:
            item = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(item)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background:{color}; border:1px solid #8a939c; border-radius:2px;"
            )
            row.addWidget(swatch)
            label = QtWidgets.QLabel(text)
            label.setObjectName("muted")
            row.addWidget(label)
            return item

        def _build_files_group(self) -> Any:
            group = QtWidgets.QGroupBox("Files")
            layout = QtWidgets.QGridLayout(group)
            layout.setColumnStretch(1, 1)

            layout.addWidget(QtWidgets.QLabel("Source model"), 0, 0)
            self.input_path_edit = QtWidgets.QLineEdit()
            self.input_path_edit.setReadOnly(True)
            self.input_path_edit.setPlaceholderText("Open an STL, 3MF, or OBJ model")
            layout.addWidget(self.input_path_edit, 0, 1)
            self.open_button = QtWidgets.QPushButton("Open…")
            self.open_button.clicked.connect(self._choose_input)
            layout.addWidget(self.open_button, 0, 2)

            layout.addWidget(QtWidgets.QLabel("Supports STL"), 1, 0)
            self.output_path_edit = QtWidgets.QLineEdit()
            self.output_path_edit.setPlaceholderText(
                "Choose the support-only .stl output"
            )
            self.output_path_edit.textEdited.connect(self._mark_output_edited)
            layout.addWidget(self.output_path_edit, 1, 1)
            self.output_button = QtWidgets.QPushButton("Save as…")
            self.output_button.clicked.connect(self._choose_output)
            layout.addWidget(self.output_button, 1, 2)
            return group

        def _build_pose_group(self) -> Any:
            group = QtWidgets.QGroupBox("Model pose")
            form = QtWidgets.QFormLayout(group)

            self.bottom_height_spin = _number_field(
                0.01, 1000.0, 25.0, step=1.0, suffix=" mm"
            )
            self.bottom_height_spin.setToolTip(
                "Vertical distance from the build plate to the model's lowest point."
            )
            form.addRow("Bottom height", self.bottom_height_spin)

            self.rotation_x_spin = _number_field(
                -360.0, 360.0, 0.0, step=5.0, suffix="°", decimals=1
            )
            self.rotation_y_spin = _number_field(
                -360.0, 360.0, 0.0, step=5.0, suffix="°", decimals=1
            )
            self.rotation_z_spin = _number_field(
                -360.0, 360.0, 0.0, step=5.0, suffix="°", decimals=1
            )
            form.addRow("Rotate X", self.rotation_x_spin)
            form.addRow("Rotate Y", self.rotation_y_spin)
            form.addRow("Rotate Z", self.rotation_z_spin)
            return group

        def _build_settings_group(self) -> Any:
            group = QtWidgets.QGroupBox("Organic support settings")
            form = QtWidgets.QFormLayout(group)

            self.layer_height_spin = _number_field(
                0.01, 1.0, 0.30, step=0.05, suffix=" mm"
            )
            self.branch_diameter_spin = _number_field(
                0.10, 100.0, 2.0, step=0.1, suffix=" mm"
            )
            self.tip_diameter_spin = _number_field(
                0.10, 100.0, 0.8, step=0.1, suffix=" mm"
            )
            self.branch_diameter_angle_spin = _number_field(
                0.0, 15.0, 15.0, step=1.0, suffix="°", decimals=1
            )
            self.branch_diameter_angle_spin.setToolTip(
                "Native PrusaSlicer radius growth toward the bed; 15° is its maximum."
            )
            self.branch_angle_spin = _number_field(
                10.0, 85.0, 40.0, step=1.0, suffix="°", decimals=1
            )
            self.branch_angle_slow_spin = _number_field(
                10.0, 85.0, 25.0, step=1.0, suffix="°", decimals=1
            )
            self.contact_distance_spin = _number_field(
                0.0, 10.0, 0.0, step=0.05, suffix=" mm"
            )

            self.branch_angle_spin.setToolTip(
                "Maximum branch angle while branches avoid the model."
            )
            self.branch_angle_slow_spin.setToolTip(
                "Preferred branch angle when no avoidance move is required."
            )
            self.contact_distance_spin.setToolTip(
                "Vertical separation between a branch contact and the model."
            )

            form.addRow("Layer height", self.layer_height_spin)
            form.addRow("Branch diameter", self.branch_diameter_spin)
            form.addRow("Trunk growth angle", self.branch_diameter_angle_spin)
            form.addRow("Tip diameter", self.tip_diameter_spin)
            form.addRow("Maximum branch angle", self.branch_angle_spin)
            form.addRow("Preferred branch angle", self.branch_angle_slow_spin)
            form.addRow("Contact distance", self.contact_distance_spin)
            self.slim_full_tip_checkbox = QtWidgets.QCheckBox(
                "Half-size roots, fuller tips"
            )
            self.slim_full_tip_checkbox.setToolTip(
                "Preset: 4 mm root lobes, 1.5 mm blob margin, 6° growth, "
                "1.8 mm branches, and 1.4 mm tips. Uncheck to restore the "
                "previous values."
            )
            self.slim_full_tip_checkbox.toggled.connect(
                self._toggle_slim_full_tip_preset
            )
            form.addRow("Branch profile", self.slim_full_tip_checkbox)
            self.enforcers_only_checkbox = QtWidgets.QCheckBox(
                "Only green painted surfaces"
            )
            self.enforcers_only_checkbox.setChecked(True)
            self.enforcers_only_checkbox.setEnabled(False)
            self.enforcers_only_checkbox.setToolTip(
                "Required: automatic overhang support is disabled when painting."
            )
            form.addRow("Painting scope", self.enforcers_only_checkbox)
            return group

        def _build_base_group(self) -> Any:
            group = QtWidgets.QGroupBox("Single Organic trunk")
            form = QtWidgets.QFormLayout(group)
            self.network_base_checkbox = QtWidgets.QCheckBox(
                "Fuse every bed root into one blob"
            )
            self.network_base_checkbox.setChecked(True)
            self.network_base_checkbox.setToolTip(
                "Create one large rounded starting mass before branches separate."
            )
            form.addRow("Network", self.network_base_checkbox)
            self.base_thickness_spin = _number_field(
                0.2, 50.0, 20.0, step=1.0, suffix=" mm", decimals=1
            )
            self.base_beam_width_spin = _number_field(
                0.5, 30.0, 3.0, step=0.5, suffix=" mm", decimals=1
            )
            self.base_node_diameter_spin = _number_field(
                0.5, 50.0, 8.0, step=0.5, suffix=" mm", decimals=1
            )
            self.base_beam_width_spin.setToolTip(
                "Extra rounded material around the fused trunk outline."
            )
            self.base_node_diameter_spin.setToolTip(
                "Rounded lobe diameter beneath each emerging Organic branch."
            )
            self.base_thickness_spin.setToolTip(
                "Height over which the full bed footprint smoothly tapers into "
                "the native Organic branches. Must remain below the model."
            )
            form.addRow("Taper height", self.base_thickness_spin)
            form.addRow("Blob margin", self.base_beam_width_spin)
            form.addRow("Root lobe diameter", self.base_node_diameter_spin)
            self.network_base_checkbox.toggled.connect(self._set_base_controls_enabled)
            self._slim_full_tip_previous: dict[str, float] | None = None
            return group

        @QtCore.Slot(bool)
        def _toggle_slim_full_tip_preset(self, enabled: bool) -> None:
            fields = {
                "root_lobe": self.base_node_diameter_spin,
                "blob_margin": self.base_beam_width_spin,
                "growth_angle": self.branch_diameter_angle_spin,
                "branch_diameter": self.branch_diameter_spin,
                "tip_diameter": self.tip_diameter_spin,
            }
            if enabled:
                self._slim_full_tip_previous = {
                    name: float(field.value()) for name, field in fields.items()
                }
                values = {
                    "root_lobe": 4.0,
                    "blob_margin": 1.5,
                    "growth_angle": 6.0,
                    "branch_diameter": 1.8,
                    "tip_diameter": 1.4,
                }
            else:
                if self._slim_full_tip_previous is None:
                    return
                values = self._slim_full_tip_previous
                self._slim_full_tip_previous = None
            for name, value in values.items():
                fields[name].setValue(value)

        @QtCore.Slot(bool)
        def _set_base_controls_enabled(self, enabled: bool) -> None:
            available = bool(enabled) and self._thread is None
            for field in (
                self.base_thickness_spin,
                self.base_beam_width_spin,
                self.base_node_diameter_spin,
            ):
                field.setEnabled(available)

        # ---- Preview and painting ------------------------------------------

        @QtCore.Slot()
        def _invalidate_generated_support(self, *_args: object) -> None:
            self.preview.clear_supports()

        @QtCore.Slot()
        def _update_preview_pose(self, *_args: object) -> None:
            self.preview.set_pose(
                self.rotation_x_spin.value(),
                self.rotation_y_spin.value(),
                self.rotation_z_spin.value(),
                self.bottom_height_spin.value(),
            )

        @QtCore.Slot(float)
        def _on_brush_radius_changed(self, value: float) -> None:
            self.preview.set_brush_radius(value)

        @QtCore.Slot(bool)
        def _on_paint_mode_changed(self, locked: bool) -> None:
            self._painting_locked = bool(locked)
            self.paint_lock_label.setVisible(self._painting_locked)
            running = self._thread is not None
            pose_enabled = not running and not self._painting_locked
            for field in (
                self.bottom_height_spin,
                self.rotation_x_spin,
                self.rotation_y_spin,
                self.rotation_z_spin,
            ):
                field.setEnabled(pose_enabled)
            for button in self.view_buttons:
                button.setEnabled(pose_enabled)
            self.open_button.setEnabled(pose_enabled)
            self.open_action.setEnabled(pose_enabled)
            self.recent_menu.setEnabled(pose_enabled and bool(self._recent_files))
            for action in self._view_actions:
                action.setEnabled(pose_enabled)
            if self._painting_locked:
                self.surface_info_label.setText(
                    "Position locked — drag the left mouse button to paint."
                )

        @QtCore.Slot(str)
        def _on_interaction_mode_changed(self, mode: str) -> None:
            if mode == PAINT_MODE_POSE:
                self.surface_info_label.setText(
                    "Pose object — drag rotates; Shift-drag rotates Z; "
                    "Option/Alt-drag or scroll changes height."
                )
            elif mode == PAINT_MODE_INSPECT:
                self.surface_info_label.setText(
                    "Orbit unlocked — click a face to read angle and height."
                )

        @QtCore.Slot(float, float, float, float)
        def _on_preview_pose_edited(
            self, x_deg: float, y_deg: float, z_deg: float, bottom_height: float
        ) -> None:
            fields = (
                (self.rotation_x_spin, x_deg),
                (self.rotation_y_spin, y_deg),
                (self.rotation_z_spin, z_deg),
                (self.bottom_height_spin, bottom_height),
            )
            blockers = [QtCore.QSignalBlocker(field) for field, _value in fields]
            for field, value in fields:
                field.setValue(value)
            del blockers

        @QtCore.Slot(int, int)
        def _on_paint_changed(self, enforcers: int, blockers: int) -> None:
            self.paint_count_label.setText(
                f"Green {enforcers:,}  •  blocked {blockers:,}"
            )

        @QtCore.Slot(float, float, float)
        def _on_surface_picked(
            self, underside_angle: float, relative_height: float, concavity: float
        ) -> None:
            self.surface_info_label.setText(
                f"↓ angle {underside_angle:.1f}°  •  height "
                f"{relative_height * 100:.0f}%  •  concavity {concavity * 100:.0f}%"
            )

        # ---- File selection -------------------------------------------------

        @QtCore.Slot()
        def _choose_input(self) -> None:
            current = self.input_path_edit.text().strip()
            start = str(Path(current).parent) if current else str(Path.home())
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Open model",
                start,
                "3D models (*.stl *.3mf *.obj);;All files (*)",
            )
            if path:
                self.set_input_path(path)

        def _refresh_recent_menu(self) -> None:
            for index, action in enumerate(self._recent_actions):
                if index < len(self._recent_files):
                    recent_path = self._recent_files[index]
                    action.setText(f"&{index + 1}  {Path(recent_path).name}")
                    action.setData(recent_path)
                    action.setToolTip(recent_path)
                    action.setVisible(True)
                else:
                    action.setVisible(False)
            has_recent = bool(self._recent_files)
            self.clear_recent_action.setEnabled(has_recent)
            self.recent_menu.setEnabled(has_recent)

        def _add_recent_file(self, model_path: Path) -> None:
            resolved = str(model_path.expanduser().resolve())
            self._recent_files = [
                item for item in self._recent_files if item != resolved
            ]
            self._recent_files.insert(0, resolved)
            del self._recent_files[_MAX_RECENT_FILES:]
            self._settings.setValue("files/recent", self._recent_files)
            self._refresh_recent_menu()

        @QtCore.Slot()
        def _clear_recent_files(self) -> None:
            self._recent_files.clear()
            self._settings.remove("files/recent")
            self._refresh_recent_menu()

        def _open_recent(self, path: str) -> None:
            if self._thread is not None or self._painting_locked:
                return
            model_path = Path(path)
            if not model_path.is_file():
                self._recent_files = [
                    item for item in self._recent_files if item != path
                ]
                self._settings.setValue("files/recent", self._recent_files)
                self._refresh_recent_menu()
                self._show_error("Recent model not found", f"No file exists at {path}")
                return
            self.set_input_path(model_path)

        def set_input_path(self, path: str | Path) -> None:
            model_path = Path(path).expanduser().resolve()
            self._set_status("Loading model and analyzing underside surfaces…")
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
            try:
                from .mesh_io import load_reference_mesh

                mesh = load_reference_mesh(model_path)
                self.preview.load_mesh(mesh)
                self._update_preview_pose()
                self.preview.view_under_isometric()
            except Exception as exc:
                self._set_status(f"Could not preview model: {exc}")
                self._show_error("Could not preview model", str(exc))
                return
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.input_path_edit.setText(str(model_path))
            self._add_recent_file(model_path)
            if not self._output_was_edited or not self.output_path_edit.text().strip():
                suggested = model_path.with_name(
                    f"{model_path.stem}_organic_supports.stl"
                )
                self.output_path_edit.setText(str(suggested))
                self._output_was_edited = False
            self._set_status(
                f"Loaded {len(mesh.faces):,} faces. Purple marks low concave "
                "undersides; green paint enforces support."
            )

        def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._thread is not None or self._painting_locked:
                event.ignore()
                return
            urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
            if any(
                Path(url.toLocalFile()).suffix.lower() in _SUPPORTED_MODEL_SUFFIXES
                for url in urls
                if url.isLocalFile()
            ):
                event.acceptProposedAction()
            else:
                event.ignore()

        def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._thread is not None or self._painting_locked:
                event.ignore()
                return
            for url in event.mimeData().urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.suffix.lower() in _SUPPORTED_MODEL_SUFFIXES:
                    self.set_input_path(path)
                    event.acceptProposedAction()
                    return
            event.ignore()

        @QtCore.Slot()
        def _choose_output(self) -> None:
            current = self.output_path_edit.text().strip()
            if current:
                start = current
            elif self.input_path_edit.text().strip():
                model_path = Path(self.input_path_edit.text().strip())
                start = str(
                    model_path.with_name(f"{model_path.stem}_organic_supports.stl")
                )
            else:
                start = str(Path.home() / "organic_supports.stl")

            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save support-only STL", start, "STL mesh (*.stl)"
            )
            if path:
                output = Path(path)
                if output.suffix.lower() != ".stl":
                    output = output.with_suffix(".stl")
                self.output_path_edit.setText(str(output))
                self._output_was_edited = True

        @QtCore.Slot()
        def _mark_output_edited(self) -> None:
            self._output_was_edited = True

        # ---- Generation -----------------------------------------------------

        def _build_job(self) -> GenerationJob:
            input_path = Path(self.input_path_edit.text().strip()).expanduser()
            output_path = Path(self.output_path_edit.text().strip()).expanduser()

            if self.preview.mesh is None:
                raise ValueError("Load the model into the 3D preview first.")

            enforcers, blockers = self.preview.painted_faces()

            # GenerationJob is imported at use time so the UI can be imported
            # independently while the native runner is being installed/built.
            from . import GenerationJob

            job = GenerationJob(
                input_path=input_path,
                output_path=output_path,
                bottom_height_mm=self.bottom_height_spin.value(),
                rotation_x_deg=self.rotation_x_spin.value(),
                rotation_y_deg=self.rotation_y_spin.value(),
                rotation_z_deg=self.rotation_z_spin.value(),
                layer_height_mm=self.layer_height_spin.value(),
                branch_diameter_mm=self.branch_diameter_spin.value(),
                branch_diameter_angle_deg=self.branch_diameter_angle_spin.value(),
                tip_diameter_mm=self.tip_diameter_spin.value(),
                branch_angle_deg=self.branch_angle_spin.value(),
                branch_angle_slow_deg=self.branch_angle_slow_spin.value(),
                contact_distance_mm=self.contact_distance_spin.value(),
                network_base_enabled=self.network_base_checkbox.isChecked(),
                base_thickness_mm=self.base_thickness_spin.value(),
                base_beam_width_mm=self.base_beam_width_spin.value(),
                base_node_diameter_mm=self.base_node_diameter_spin.value(),
                painted_enforcer_faces=enforcers,
                painted_blocker_faces=blockers,
                paint_face_count=self.preview.face_count,
                paint_mesh_fingerprint=self.preview.fingerprint,
                enforcers_only=True,
            )
            # GUI and CLI deliberately share this single validation path.
            return job.validated()

        @QtCore.Slot()
        def _start_generation(self) -> None:
            if self._thread is not None:
                return
            try:
                job = self._build_job()
            except Exception as exc:
                self._show_error("Cannot generate supports", str(exc))
                return

            output_path = Path(job.output_path)
            if output_path.exists():
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Replace existing STL?",
                    f"{output_path.name} already exists. Replace it?",
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
                )
                if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                    return

            self._active_output = output_path
            self.preview.clear_supports()
            thread = QtCore.QThread(self)
            worker = GenerationWorker(job, generate_fn=self._generate_fn)
            worker.moveToThread(thread)

            thread.started.connect(worker.run)
            worker.progress.connect(self._on_progress)
            worker.finished.connect(self._on_finished)
            worker.failed.connect(self._on_failed)
            worker.cancelled.connect(self._on_cancelled)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.cancelled.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.cancelled.connect(worker.deleteLater)
            thread.finished.connect(self._on_thread_finished)
            thread.finished.connect(thread.deleteLater)

            self._thread = thread
            self._worker = worker
            self._set_running(True)
            self._set_status("Preparing native organic-support generation…")
            thread.start()

        @QtCore.Slot()
        def _cancel_generation(self) -> None:
            if self._worker is None:
                return
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelling after the current processing stage…")

        @QtCore.Slot(str)
        def _on_progress(self, message: str) -> None:
            if message.strip():
                self._set_status(message)

        @QtCore.Slot(object)
        def _on_finished(self, result: object) -> None:
            output = Path(getattr(result, "output_path", self._active_output or ""))
            warnings = tuple(getattr(result, "warnings", ()))
            try:
                support_mesh = load_support_preview_mesh(output)
                self.preview.load_support_mesh(support_mesh)
            except Exception as exc:
                self._set_status(
                    f"Created support-only STL, but could not display it: {exc}"
                )
            else:
                nodes = int(getattr(result, "base_node_count", 0))
                base_detail = (
                    f" Single trunk fused beneath {nodes} Organic roots."
                    if nodes
                    else ""
                )
                self._set_status(
                    f"Created and displayed support-only STL: {output}.{base_detail}"
                    + (f" WARNING: {' '.join(warnings)}" if warnings else "")
                )
            if warnings:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Stand exceeds build volume",
                    "\n\n".join(warnings)
                    + "\n\nThe full stand STL was generated anyway.",
                )
            self.generationCompleted.emit(result)

        @QtCore.Slot(str)
        def _on_failed(self, message: str) -> None:
            self._set_status(f"Generation failed: {message}")
            self._show_error("Support generation failed", message)

        @QtCore.Slot()
        def _on_cancelled(self) -> None:
            self._set_status("Generation cancelled. No completed STL was produced.")

        @QtCore.Slot()
        def _on_thread_finished(self) -> None:
            self._thread = None
            self._worker = None
            self._active_output = None
            self._set_running(False)
            if self._close_when_idle and self._diagnostics_thread is None:
                self._close_when_idle = False
                QtCore.QTimer.singleShot(0, self.close)

        def _diagnostics_output_dir(self) -> Path:
            text = self.output_path_edit.text().strip()
            if not text:
                return Path.home()
            candidate = Path(text).expanduser()
            return (candidate if candidate.is_dir() else candidate.parent).resolve()

        def _present_diagnostics(self, report: object) -> None:
            ready = bool(getattr(report, "desktop_ok", False))
            message = QtWidgets.QMessageBox(self)
            message.setWindowTitle("HolderPro diagnostics")
            message.setIcon(
                QtWidgets.QMessageBox.Icon.Information
                if ready
                else QtWidgets.QMessageBox.Icon.Warning
            )
            message.setText(
                "HolderPro is ready to generate and display supports."
                if ready
                else "HolderPro found one or more desktop setup problems."
            )
            message.setDetailedText(str(getattr(report, "to_text")()))
            message.exec()

        def _start_diagnostics(
            self, *, show_dialog: bool = False, first_run: bool = False
        ) -> None:
            self._diagnostics_show_dialog |= show_dialog
            self._diagnostics_first_run |= first_run
            if self._diagnostics_thread is not None:
                self._set_status("Diagnostics are already running…")
                return

            thread = QtCore.QThread(self)
            worker = DiagnosticsWorker(self._diagnostics_output_dir())
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(self._on_diagnostics_finished)
            worker.failed.connect(self._on_diagnostics_failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            thread.finished.connect(self._on_diagnostics_thread_finished)
            thread.finished.connect(thread.deleteLater)
            self._diagnostics_thread = thread
            self._diagnostics_worker = worker
            self._set_status("Checking engine, output permissions, and graphics…")
            thread.start()

        @QtCore.Slot(object)
        def _on_diagnostics_finished(self, report: DoctorReport) -> None:
            show_dialog = self._diagnostics_show_dialog
            first_run = self._diagnostics_first_run
            self._diagnostics_show_dialog = False
            self._diagnostics_first_run = False
            if first_run:
                self._settings.setValue("diagnostics/firstRunComplete", True)
            ready = bool(getattr(report, "desktop_ok", False))
            self._set_status(
                "HolderPro is ready. Open a model, pose it, and paint green support regions."
                if ready
                else "Diagnostics found a desktop setup problem; open Help → Run diagnostics."
            )
            if show_dialog or (first_run and not ready):
                self._present_diagnostics(report)
            if self._pending_diagnostics_export is not None:
                path = self._pending_diagnostics_export
                self._pending_diagnostics_export = None
                self._write_diagnostics_export(path, report)

        @QtCore.Slot(str)
        def _on_diagnostics_failed(self, error: str) -> None:
            should_alert = bool(
                self._diagnostics_show_dialog
                or self._diagnostics_first_run
                or self._pending_diagnostics_export is not None
            )
            self._diagnostics_show_dialog = False
            self._diagnostics_first_run = False
            self._pending_diagnostics_export = None
            self._set_status(f"Diagnostics could not finish: {error}")
            if should_alert:
                self._show_error("Diagnostics failed", error)

        @QtCore.Slot()
        def _on_diagnostics_thread_finished(self) -> None:
            self._diagnostics_thread = None
            self._diagnostics_worker = None
            if self._close_when_idle and self._thread is None:
                self._close_when_idle = False
                QtCore.QTimer.singleShot(0, self.close)

        @QtCore.Slot()
        def _show_diagnostics(self) -> None:
            self._start_diagnostics(show_dialog=True)

        @QtCore.Slot()
        def _run_first_diagnostics(self) -> None:
            self._start_diagnostics(first_run=True)

        def _write_diagnostics_export(
            self, path: Path, report: DoctorReport
        ) -> None:
            try:
                from .diagnostics import export_diagnostics

                exported = Path(
                    export_diagnostics(path, report=report, redact_paths=True)
                ).expanduser()
            except Exception as exc:
                self._show_error("Could not export diagnostics", str(exc))
                return
            self._set_status(
                f"Exported redacted diagnostics to {exported}. No model geometry "
                "was included."
            )

        @QtCore.Slot()
        def _export_diagnostics(self) -> None:
            start = str(Path.home() / "holderpro-diagnostics.json")
            destination, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Export redacted diagnostics",
                start,
                "JSON diagnostics (*.json)",
            )
            if not destination:
                return
            path = Path(destination)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")
            self._pending_diagnostics_export = path
            self._start_diagnostics()

        @QtCore.Slot()
        def _show_about(self) -> None:
            from . import __version__
            from .engine import (
                PINNED_PRUSASLICER_COMMIT,
                PINNED_PRUSASLICER_VERSION,
            )

            release_url = f"{_RELEASES_URL}/tag/{_release_tag(__version__)}"
            source_url = f"{_SOURCE_URL}/tree/{_release_tag(__version__)}"
            notices_url = (
                f"{_SOURCE_URL}/blob/{_release_tag(__version__)}/"
                "THIRD_PARTY_NOTICES.md"
            )
            text = f"""
                <h2>HolderPro {__version__}</h2>
                <p>Copyright © 2026 Finn. HolderPro is free software licensed
                under the GNU Affero General Public License, version 3 or later,
                and comes with no warranty.</p>
                <p><a href="{release_url}">Version-specific release and
                corresponding-source archive</a> ·
                <a href="{source_url}">exact HolderPro source tree</a> ·
                <a href="{_SOURCE_URL}">project repository</a></p>
                <p>HolderPro uses the unmodified PrusaSlicer
                {PINNED_PRUSASLICER_VERSION} Organic-support implementation
                through a headless adapter.</p>
                <p>PrusaSlicer upstream commit:<br>
                <code>{PINNED_PRUSASLICER_COMMIT}</code></p>
                <p>PrusaSlicer is © Prusa Research and contributors and is used
                under AGPL-3.0. HolderPro is an independent project; it is not
                affiliated with or endorsed by Prusa Research. Prusa names and
                marks belong to their respective owners.</p>
                <p>STL files generated by HolderPro are not automatically covered
                by the AGPL merely because HolderPro produced them. See the
                bundled <a href="{notices_url}">Third-Party Notices</a> for
                complete attribution.</p>
            """
            QtWidgets.QMessageBox.about(self, "About / Legal Notices", text)

        def _set_running(self, running: bool) -> None:
            self.generate_button.setEnabled(not running)
            self.generate_action.setEnabled(not running)
            self.cancel_button.setEnabled(running)
            self.open_button.setEnabled(not running and not self._painting_locked)
            self.open_action.setEnabled(not running and not self._painting_locked)
            self.recent_menu.setEnabled(
                not running and not self._painting_locked and bool(self._recent_files)
            )
            self.output_button.setEnabled(not running)
            self.input_path_edit.setEnabled(not running)
            self.output_path_edit.setEnabled(not running)
            self.preview.setEnabled(not running)
            self.brush_radius_spin.setEnabled(not running)
            self.low_height_spin.setEnabled(not running)
            self.clear_paint_button.setEnabled(not running)
            self.enforcers_only_checkbox.setEnabled(False)
            self.slim_full_tip_checkbox.setEnabled(not running)
            self.network_base_checkbox.setEnabled(not running)
            self._set_base_controls_enabled(
                not running and self.network_base_checkbox.isChecked()
            )
            for button in self.paint_buttons:
                button.setEnabled(not running)
            for action in self._mode_actions:
                action.setEnabled(not running)
            self.clear_paint_action.setEnabled(not running)
            pose_enabled = not running and not self._painting_locked
            for field in (
                self.bottom_height_spin,
                self.rotation_x_spin,
                self.rotation_y_spin,
                self.rotation_z_spin,
            ):
                field.setEnabled(pose_enabled)
            for button in self.view_buttons:
                button.setEnabled(pose_enabled)
            for action in self._view_actions:
                action.setEnabled(pose_enabled)
            self.progress_bar.setRange(0, 0 if running else 1)
            if not running:
                self.progress_bar.setValue(0)

        def _set_status(self, message: str) -> None:
            self.status_label.setText(message)

        def _show_error(self, title: str, message: str) -> None:
            QtWidgets.QMessageBox.critical(self, title, message)

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
            if self._thread is None and self._diagnostics_thread is None:
                self._save_settings()
                event.accept()
                return
            self._close_when_idle = True
            if self._thread is not None:
                self._cancel_generation()
            event.ignore()


else:

    class OrganicSupportsWindow:  # type: ignore[no-redef]  # pragma: no cover
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            require_pyside6()


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the supports-only desktop form."""

    try:
        require_pyside6()
        from .preview import require_preview_dependencies

        require_preview_dependencies()
    except RuntimeError as exc:
        print(f"holderpro-gui: {exc}", file=sys.stderr)
        return 2
    assert QtWidgets is not None and QtGui is not None
    args = list(sys.argv if argv is None else argv)
    existing_app = QtWidgets.QApplication.instance()
    app = (
        existing_app
        if isinstance(existing_app, QtWidgets.QApplication)
        else QtWidgets.QApplication(args)
    )
    app.setApplicationName("HolderPro")
    app.setApplicationDisplayName("HolderPro")
    app.setOrganizationName("HolderPro")
    icon_path = _application_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    initial_path = args[1] if len(args) > 1 else None
    window = OrganicSupportsWindow(initial_path=initial_path)
    window.show()
    return int(app.exec())


__all__ = ["OrganicSupportsWindow", "main", "require_pyside6"]


if __name__ == "__main__":  # pragma: no cover - manual desktop entry point
    raise SystemExit(main())
