import json
from pathlib import Path

from qtpy import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
import numpy as np

from pymodaq_gui import utils as gutils
from pymodaq_utils.config import Config, ConfigError
from pymodaq_utils.logger import set_logger, get_module_name

from pymodaq.extensions.utils import CustomExt

logger = set_logger(get_module_name(__file__))
main_config = Config()

EXTENSION_NAME = 'Dual Axis Controller'
CLASS_NAME = 'DualAxisController'


UNIT_MULTIPLIERS = {
    'nm':    1e-3,
    'µm':    1,
    'mm':    1e3,
    'a.u.':  1.0,
    'steps': 1.0,
    'mrad':  1.0,
    'rad':   1000.0,
    '°':     1.0,
}


# ---------------------------------------------------------------------------
# Single-axis control widget
# ---------------------------------------------------------------------------
class ActuatorWidget(QtWidgets.QGroupBox):
    move_requested = QtCore.Signal(float)   # absolute, base units
    step_requested = QtCore.Signal(float)   # signed relative, base units
    stop_requested = QtCore.Signal()

    def __init__(self, axis_name: str, parent=None):
        super().__init__(axis_name, parent)
        self.axis_name = axis_name
        self._unit_multiplier = 1e3
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)

        # Unit
        unit_layout = QtWidgets.QHBoxLayout()
        unit_layout.addWidget(QtWidgets.QLabel("Unit:"))
        self.unit_combo = QtWidgets.QComboBox()
        self.unit_combo.addItems(list(UNIT_MULTIPLIERS.keys()))
        self.unit_combo.setCurrentText('mm')
        unit_layout.addWidget(self.unit_combo)
        layout.addLayout(unit_layout)

        # Current position
        pos_layout = QtWidgets.QHBoxLayout()
        pos_layout.addWidget(QtWidgets.QLabel("Current:"))
        self.current_pos_label = QtWidgets.QLabel("N/A")
        self.current_pos_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        font = QtGui.QFont(); font.setBold(True)
        self.current_pos_label.setFont(font)
        self.current_unit_label = QtWidgets.QLabel("mm")
        pos_layout.addWidget(self.current_pos_label)
        pos_layout.addWidget(self.current_unit_label)
        layout.addLayout(pos_layout)

        # Target
        target_layout = QtWidgets.QHBoxLayout()
        target_layout.addWidget(QtWidgets.QLabel("Target:"))
        self.target_spinbox = QtWidgets.QDoubleSpinBox()
        self.target_spinbox.setRange(-1e9, 1e9)
        self.target_spinbox.setDecimals(3)
        self.target_spinbox.setSingleStep(1)
        self.target_unit_label = QtWidgets.QLabel("mm")
        target_layout.addWidget(self.target_spinbox)
        target_layout.addWidget(self.target_unit_label)
        layout.addLayout(target_layout)

        # Step
        step_layout = QtWidgets.QHBoxLayout()
        step_layout.addWidget(QtWidgets.QLabel("Step:"))
        self.step_spinbox = QtWidgets.QDoubleSpinBox()
        self.step_spinbox.setRange(1e-9, 1e9)
        self.step_spinbox.setDecimals(3)
        self.step_spinbox.setSingleStep(1)
        self.step_spinbox.setValue(1.0)
        self.step_unit_label = QtWidgets.QLabel("mm")
        step_layout.addWidget(self.step_spinbox)
        step_layout.addWidget(self.step_unit_label)
        layout.addLayout(step_layout)

        # Move Abs + Stop
        abs_layout = QtWidgets.QHBoxLayout()
        self.btn_move_abs = QtWidgets.QPushButton("Move Abs")
        self.btn_move_abs.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold;")
        self.btn_stop = QtWidgets.QPushButton("STOP")
        self.btn_stop.setStyleSheet(
            "background-color: #c0392b; color: white; font-weight: bold;")
        abs_layout.addWidget(self.btn_move_abs)
        abs_layout.addWidget(self.btn_stop)
        layout.addLayout(abs_layout)

        self.status_label = QtWidgets.QLabel("Idle")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.btn_move_abs.clicked.connect(self._on_move_abs)
        self.btn_stop.clicked.connect(self.stop_requested)
        self.unit_combo.currentTextChanged.connect(self._on_unit_changed)

    def _on_move_abs(self):
        self.move_requested.emit(self.target_spinbox.value() * self._unit_multiplier)

    def _on_unit_changed(self, unit: str):
        self._unit_multiplier = UNIT_MULTIPLIERS.get(unit, 1.0)
        for lbl in (self.current_unit_label, self.target_unit_label, self.step_unit_label):
            lbl.setText(unit)

    def update_position(self, base_position: float):
        display = base_position / self._unit_multiplier
        self.current_pos_label.setText(f"{display:.4f}")
        self.current_unit_label.setText(self.unit_combo.currentText())

    def set_status(self, text: str):
        self.status_label.setText(text)

    @property
    def step_base(self) -> float:
        return self.step_spinbox.value() * self._unit_multiplier

    @property
    def current_base(self) -> float:
        try:
            return float(self.current_pos_label.text()) * self._unit_multiplier
        except ValueError:
            return 0.0


# ---------------------------------------------------------------------------
# Cross-shaped jog pad
# ---------------------------------------------------------------------------
class JogPad(QtWidgets.QWidget):
    left_requested  = QtCore.Signal()
    right_requested = QtCore.Signal()
    up_requested    = QtCore.Signal()
    down_requested  = QtCore.Signal()
    stop_requested  = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setSpacing(4)

        s_mv = "font-size: 18px; font-weight: bold; min-width: 52px; min-height: 52px;"
        s_st = s_mv + "background-color: #c0392b; color: white; font-size: 12px;"

        self.btn_up    = QtWidgets.QPushButton("▲\nY+")
        self.btn_down  = QtWidgets.QPushButton("▼\nY−")
        self.btn_left  = QtWidgets.QPushButton("◀\nX−")
        self.btn_right = QtWidgets.QPushButton("▶\nX+")
        self.btn_stop  = QtWidgets.QPushButton("■\nSTOP")

        for btn in (self.btn_up, self.btn_down, self.btn_left, self.btn_right):
            btn.setStyleSheet(s_mv)
        self.btn_stop.setStyleSheet(s_st)

        grid.addWidget(self.btn_up,    0, 1)
        grid.addWidget(self.btn_left,  1, 0)
        grid.addWidget(self.btn_stop,  1, 1)
        grid.addWidget(self.btn_right, 1, 2)
        grid.addWidget(self.btn_down,  2, 1)

        self.btn_left.clicked.connect(self.left_requested)
        self.btn_right.clicked.connect(self.right_requested)
        self.btn_up.clicked.connect(self.up_requested)
        self.btn_down.clicked.connect(self.down_requested)
        self.btn_stop.clicked.connect(self.stop_requested)


# ---------------------------------------------------------------------------
# Saved positions panel
# ---------------------------------------------------------------------------
class SavedPositionsWidget(QtWidgets.QWidget):
    """List of named (x, y) positions with Save / Go / Delete."""

    go_requested = QtCore.Signal(float, float)   # base units

    def __init__(self, positions_file: Path, parent=None):
        super().__init__(parent)
        self._file = positions_file
        self._positions: dict = {}   # name -> {"x": float, "y": float}  (base units)
        self._setup_ui()
        self._load_from_file()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)

        # Table: Name | X | Y | buttons
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "X (base)", "Y (base)", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        # Bottom: name entry + Save current button
        save_row = QtWidgets.QHBoxLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Position name…")
        self.btn_save = QtWidgets.QPushButton("💾  Save current")
        self.btn_save.setStyleSheet("font-weight: bold;")
        save_row.addWidget(self.name_edit)
        save_row.addWidget(self.btn_save)
        layout.addLayout(save_row)

        self.btn_save.clicked.connect(self._on_save)

    # ------------------------------------------------------------------
    def set_current_position(self, x_base: float, y_base: float):
        self._current_x = x_base
        self._current_y = y_base

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Name required", "Please enter a name.")
            return
        x = getattr(self, '_current_x', 0.0)
        y = getattr(self, '_current_y', 0.0)
        self._positions[name] = {"x": x, "y": y}
        self._save_to_file()
        self._refresh_table()

    def _refresh_table(self):
        self.table.setRowCount(0)
        for name, pos in self._positions.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{pos['x']:.5g}"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{pos['y']:.5g}"))

            # Action buttons in a cell widget
            cell_w = QtWidgets.QWidget()
            cell_l = QtWidgets.QHBoxLayout(cell_w)
            cell_l.setContentsMargins(2, 1, 2, 1)
            cell_l.setSpacing(3)

            btn_go = QtWidgets.QPushButton("Go")
            btn_go.setStyleSheet(
                "background-color: #27ae60; color: white; font-weight: bold; padding: 2px 6px;")
            btn_del = QtWidgets.QPushButton("✕")
            btn_del.setStyleSheet(
                "background-color: #c0392b; color: white; font-weight: bold; padding: 2px 6px;")

            _name = name   # capture
            btn_go.clicked.connect(lambda _, n=_name: self._on_go(n))
            btn_del.clicked.connect(lambda _, n=_name: self._on_delete(n))

            cell_l.addWidget(btn_go)
            cell_l.addWidget(btn_del)
            self.table.setCellWidget(row, 3, cell_w)

        self.table.resizeColumnToContents(3)

    def _on_go(self, name: str):
        pos = self._positions.get(name)
        if pos:
            self.go_requested.emit(pos["x"], pos["y"])

    def _on_delete(self, name: str):
        reply = QtWidgets.QMessageBox.question(
            self, "Delete position",
            f"Delete '{name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._positions.pop(name, None)
            self._save_to_file()
            self._refresh_table()

    def _save_to_file(self):
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, 'w') as f:
                json.dump(self._positions, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save positions: {e}")

    def _load_from_file(self):
        try:
            if self._file.exists():
                with open(self._file) as f:
                    self._positions = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load positions: {e}")
        self._refresh_table()


# ---------------------------------------------------------------------------
# 2-D stage map widget
# ---------------------------------------------------------------------------
class StageMapWidget(QtWidgets.QWidget):
    """pyqtgraph PlotWidget showing current XY position vs stage limits."""

    def __init__(self, limits_file: Path, parent=None):
        super().__init__(parent)
        self._file = limits_file
        self._setup_ui()
        self._x = 0.0
        self._y = 0.0

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Limit inputs
        lim_group = QtWidgets.QGroupBox("Stage limits (base units)")
        lim_layout = QtWidgets.QFormLayout(lim_group)

        def make_spin(val):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(-1e9, 1e9)
            sb.setDecimals(3)
            sb.setValue(val)
            return sb
        xmin, xmax, ymin, ymax = self._load_limits()
        self.x_min_sb = make_spin(xmin)
        self.x_max_sb = make_spin(xmax)
        self.y_min_sb = make_spin(ymin)
        self.y_max_sb = make_spin(ymax)
        # self._update_limits()


        x_row = QtWidgets.QHBoxLayout()
        x_row.addWidget(self.x_min_sb)
        x_row.addWidget(QtWidgets.QLabel("≤ X ≤"))
        x_row.addWidget(self.x_max_sb)

        y_row = QtWidgets.QHBoxLayout()
        y_row.addWidget(self.y_min_sb)
        y_row.addWidget(QtWidgets.QLabel("≤ Y ≤"))
        y_row.addWidget(self.y_max_sb)

        lim_layout.addRow("X limits:", x_row)
        lim_layout.addRow("Y limits:", y_row)

        btn_apply = QtWidgets.QPushButton("Apply limits")
        btn_apply.clicked.connect(self._update_limits)
        lim_layout.addRow(btn_apply)
        layout.addWidget(lim_group)
        btn_save = QtWidgets.QPushButton("Save limits")
        btn_save.clicked.connect(self._save_limits)
        lim_layout.addRow(btn_save)

        # pyqtgraph plot
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setAspectLocked(True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', 'X', units='base')
        self.plot_widget.setLabel('left',   'Y', units='base')
        self.plot_widget.setBackground('#1e1e2e')

        # Stage boundary rectangle
        self._rect = QtWidgets.QGraphicsRectItem(xmin, ymin, xmax - xmin, ymax - ymin)
        self._rect.setPen(pg.mkPen('#74c7ec', width=2, style=QtCore.Qt.DashLine))
        self._rect.setBrush(pg.mkBrush('#1e1e2e00'))
        self.plot_widget.addItem(self._rect)

        # Saved positions scatter
        self._saved_scatter = pg.ScatterPlotItem(
            size=10, pen=pg.mkPen('#a6e3a1', width=1),
            brush=pg.mkBrush('#a6e3a150'), symbol='d')
        self.plot_widget.addItem(self._saved_scatter)

        # Current position marker
        self._pos_marker = pg.ScatterPlotItem(
            size=14, pen=pg.mkPen('#f38ba8', width=2),
            brush=pg.mkBrush('#f38ba8'), symbol='+')
        self.plot_widget.addItem(self._pos_marker)

        # Cross-hair lines
        self._vline = pg.InfiniteLine(angle=90, pen=pg.mkPen('#f38ba880', width=1))
        self._hline = pg.InfiniteLine(angle=0,  pen=pg.mkPen('#f38ba880', width=1))
        self.plot_widget.addItem(self._vline)
        self.plot_widget.addItem(self._hline)

        # Position label on plot
        self._pos_text = pg.TextItem('', color='#cdd6f4', anchor=(0, 1))
        self.plot_widget.addItem(self._pos_text)

        layout.addWidget(self.plot_widget)

        self._update_limits()

    def _update_limits(self):
        xmin = self.x_min_sb.value()
        xmax = self.x_max_sb.value()
        ymin = self.y_min_sb.value()
        ymax = self.y_max_sb.value()
        self._rect.setRect(xmin, ymin, xmax - xmin, ymax - ymin)
        self.plot_widget.setXRange(xmin - 0.05 * (xmax - xmin),
                                   xmax + 0.05 * (xmax - xmin))
        self.plot_widget.setYRange(ymin - 0.05 * (ymax - ymin),
                                   ymax + 0.05 * (ymax - ymin))

    def _save_limits(self):
        data = {
            "xmin": self.x_min_sb.value(),
            "xmax": self.x_max_sb.value(),
            "ymin": self.y_min_sb.value(),
            "ymax": self.y_max_sb.value(),
        }
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save limits: {e}")

    def _load_limits(self):
        # valeurs par défaut
        xmin, xmax, ymin, ymax = -10.0, 10.0, -10.0, 10.0
        try:
            if self._file.exists():
                with open(self._file) as f:
                    data = json.load(f)
                xmin = data["xmin"]
                xmax = data["xmax"]
                ymin = data["ymin"]
                ymax = data["ymax"]
        except Exception as e:
            logger.warning(f"Could not load limits: {e}")
        return xmin, xmax, ymin, ymax
    
    def update_position(self, x: float, y: float):
        self._x, self._y = x, y
        self._pos_marker.setData([x], [y])
        self._vline.setValue(x)
        self._hline.setValue(y)
        self._pos_text.setText(f"  ({x:.4g}, {y:.4g})")
        self._pos_text.setPos(x, y)

    def update_saved_positions(self, positions: dict):
        """positions: {name: {x, y}} in base units."""
        if not positions:
            self._saved_scatter.setData([], [])
            return
        xs = [p['x'] for p in positions.values()]
        ys = [p['y'] for p in positions.values()]
        tips = [f"{n}\n({p['x']:.4g}, {p['y']:.4g})"
                for n, p in positions.items()]
        self._saved_scatter.setData(xs, ys, tip=tips)


# ===========================================================================
# Main extension class
# ===========================================================================
class DualAxisController(CustomExt):
    """
    Two-axis stage controller:
    - Per-axis unit selector
    - Cross jog pad + keyboard control (arrows / Space)
    - Saved positions (persisted to JSON)
    - 2-D stage map with configurable limits
    """

    params = []

    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)
        self._actuator_x = ''
        self._actuator_y = ''
        path = Path(str(self.dashboard.preset_file))
        preset_name = path.stem
        self.POSITIONS_FILE = Path.home() / '.pymodaq' / f'dual_axis_{preset_name}_positions.json'
        self.LIMITS_FILE = Path.home() / '.pymodaq' / f'dual_axis_{preset_name}_limits.json'
        self.setup_ui()
    # ------------------------------------------------------------------
    # CustomExt mandatory overrides
    # ------------------------------------------------------------------
    def setup_docks(self):
        # ── Dock 1 : axis control + jog pad ────────────────────────────
        self.docks['control'] = gutils.Dock('Axis Control')
        self.dockarea.addDock(self.docks['control'])

        ctrl_w = QtWidgets.QWidget()
        ctrl_layout = QtWidgets.QVBoxLayout(ctrl_w)
        ctrl_layout.setContentsMargins(8, 8, 8, 8)
        ctrl_layout.setSpacing(8)

        title = QtWidgets.QLabel(EXTENSION_NAME)
        title.setAlignment(QtCore.Qt.AlignCenter)
        f = QtGui.QFont(); f.setPointSize(13); f.setBold(True)
        title.setFont(f)
        ctrl_layout.addWidget(title)

        axes_row = QtWidgets.QHBoxLayout()
        self.widget_x = ActuatorWidget("X Axis")
        self.widget_y = ActuatorWidget("Y Axis")
        axes_row.addWidget(self.widget_x)
        axes_row.addWidget(self.widget_y)
        ctrl_layout.addLayout(axes_row)

        jog_row = QtWidgets.QHBoxLayout()
        jog_row.addStretch()
        self.jog_pad = JogPad()
        jog_row.addWidget(self.jog_pad)

        kb_group = QtWidgets.QGroupBox("Keyboard control")
        kb_layout = QtWidgets.QVBoxLayout(kb_group)
        self.kb_enabled_cb = QtWidgets.QCheckBox("Enabled")
        self.kb_enabled_cb.setChecked(True)
        kb_layout.addWidget(self.kb_enabled_cb)
        kb_layout.addWidget(QtWidgets.QLabel(
            "← / →  : X axis\n↑ / ↓   : Y axis\nSpace  : STOP ALL"))
        jog_row.addWidget(kb_group)
        jog_row.addStretch()
        ctrl_layout.addLayout(jog_row)

        sel_group = QtWidgets.QGroupBox("Actuator assignment")
        sel_layout = QtWidgets.QFormLayout(sel_group)
        self.combo_x = QtWidgets.QComboBox()
        self.combo_y = QtWidgets.QComboBox()
        sel_layout.addRow("X Axis:", self.combo_x)
        sel_layout.addRow("Y Axis:", self.combo_y)
        self.btn_apply = QtWidgets.QPushButton("Apply assignment")
        sel_layout.addRow(self.btn_apply)
        ctrl_layout.addWidget(sel_group)

        self.statusbar = QtWidgets.QStatusBar()
        self.statusbar.showMessage("Ready.")
        ctrl_layout.addWidget(self.statusbar)

        self.docks['control'].addWidget(ctrl_w)

        ctrl_w.setFocusPolicy(QtCore.Qt.StrongFocus)
        ctrl_w.installEventFilter(self)
        self._ctrl_w = ctrl_w

        # ── Dock 2 : saved positions ────────────────────────────────────
        self.docks['positions'] = gutils.Dock('Saved Positions')
        self.dockarea.addDock(self.docks['positions'], 'bottom', self.docks['control'])

        self.saved_pos_widget = SavedPositionsWidget(self.POSITIONS_FILE)
        self.docks['positions'].addWidget(self.saved_pos_widget)

        # ── Dock 3 : stage map ──────────────────────────────────────────
        self.docks['map'] = gutils.Dock('Stage Map')
        self.dockarea.addDock(self.docks['map'], 'right', self.docks['control'])

        self.stage_map = StageMapWidget(self.LIMITS_FILE)
        self.docks['map'].addWidget(self.stage_map)

    def setup_actions(self):
        self.add_action('stop_all', 'Stop All', 'stop', "Emergency stop all actuators")
        self.add_action('quit',     'Quit',     'close2', "Quit extension")

    def connect_things(self):
        # Axis widgets
        self.widget_x.move_requested.connect(self._move_x)
        self.widget_y.move_requested.connect(self._move_y)
        self.widget_x.stop_requested.connect(self._stop_x)
        self.widget_y.stop_requested.connect(self._stop_y)

        # Jog pad
        self.jog_pad.left_requested.connect( lambda: self._step_x(-self.widget_x.step_base))
        self.jog_pad.right_requested.connect(lambda: self._step_x(+self.widget_x.step_base))
        self.jog_pad.up_requested.connect(   lambda: self._step_y(+self.widget_y.step_base))
        self.jog_pad.down_requested.connect( lambda: self._step_y(-self.widget_y.step_base))
        self.jog_pad.stop_requested.connect(self._stop_all)

        # Saved positions
        self.saved_pos_widget.go_requested.connect(self._go_to_saved)
        # Refresh map when positions table changes (save / delete)
        self.saved_pos_widget.btn_save.clicked.connect(self._refresh_map_saved)

        # Assignment
        self.btn_apply.clicked.connect(self._apply_assignment)

        # Toolbar
        self.get_action('stop_all').triggered.connect(self._stop_all)
        self.get_action('quit').triggered.connect(self.parent.close)

        self._populate_combos()
        self._refresh_map_saved()

    def setup_menu(self, menubar: QtWidgets.QMenuBar = None):
        if menubar is None:
            return
        ctrl_menu = menubar.addMenu('Control')
        self.affect_to('stop_all', ctrl_menu)
        ctrl_menu.addSeparator()
        self.affect_to('quit', ctrl_menu)

    def value_changed(self, param):
        pass

    # ------------------------------------------------------------------
    # Keyboard event filter
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if (event.type() == QtCore.QEvent.KeyPress
                and self.kb_enabled_cb.isChecked()
                and not event.isAutoRepeat()):
            key = event.key()
            if key == QtCore.Qt.Key_Left:  self._step_x(-self.widget_x.step_base); return True
            if key == QtCore.Qt.Key_Right: self._step_x(+self.widget_x.step_base); return True
            if key == QtCore.Qt.Key_Up:    self._step_y(+self.widget_y.step_base); return True
            if key == QtCore.Qt.Key_Down:  self._step_y(-self.widget_y.step_base); return True
            if key == QtCore.Qt.Key_Space: self._stop_all();                       return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------
    def _populate_combos(self):
        names = self.modules_manager.actuators_name
        for combo in (self.combo_x, self.combo_y):
            combo.clear()
            combo.addItems(names)
        if len(names) > 0: self.combo_x.setCurrentIndex(0)
        if len(names) > 1: self.combo_y.setCurrentIndex(1)
        self._apply_assignment()

    def _apply_assignment(self):
        self._actuator_x = self.combo_x.currentText()
        self._actuator_y = self.combo_y.currentText()
        msg = f"X → '{self._actuator_x}'  |  Y → '{self._actuator_y}'"
        self.statusbar.showMessage(msg)
        logger.info(msg)
        self._connect_position_signals()

    def _connect_position_signals(self):
        _SIG = ('current_value_signal', 'move_done_signal', 'position_signal')
        for mod_name, widget in ((self._actuator_x, self.widget_x),
                                 (self._actuator_y, self.widget_y)):
            mod = self._get_module(mod_name)
            if mod is None:
                continue
            for sig_name in _SIG:
                sig = getattr(mod, sig_name, None)
                if sig is None:
                    continue
                try:
                    def make_slot(w):
                        def slot(val):
                            try:
                                pos = (float(val.value()) if hasattr(val, 'value')
                                       else float(val.data) if hasattr(val, 'data')
                                       else float(val))
                                w.update_position(pos)
                                self._update_map()
                                self.saved_pos_widget.set_current_position(
                                    self.widget_x.current_base,
                                    self.widget_y.current_base)
                            except Exception as e:
                                logger.warning(f"Position slot error: {e}")
                        return slot
                    sig.connect(make_slot(widget))
                    logger.info(f"Connected '{sig_name}' for '{mod_name}'")
                except Exception as e:
                    logger.warning(f"Could not connect {sig_name}: {e}")
                break

    # ------------------------------------------------------------------
    # Module access
    # ------------------------------------------------------------------
    def _get_module(self, name: str):
        if not name:
            return None
        try:
            return self.modules_manager.get_mod_from_name(name, mod='act')
        except Exception as e:
            logger.warning(f"Could not find actuator '{name}': {e}")
            return None

    # ------------------------------------------------------------------
    # Move / step / stop
    # ------------------------------------------------------------------
    @staticmethod
    def _call_move(mod, position: float):
        if hasattr(mod, 'move_abs'):
            mod.move_abs(position)
        elif hasattr(mod, 'set_value'):
            mod.set_value(position)
        else:
            raise AttributeError(f"No move method on {mod!r}")

    @staticmethod
    def _call_stop(mod):
        for method in ('stop', 'stop_motion', 'move_stop'):
            fn = getattr(mod, method, None)
            if callable(fn):
                fn(); return

    def _move_x(self, position: float):
        mod = self._get_module(self._actuator_x)
        if mod:
            try:
                self._call_move(mod, position)
                self.widget_x.set_status(f"→ {position:.4f}")
                self.statusbar.showMessage(f"X moving to {position:.4f}")
            except Exception as e:
                logger.warning(f"Move X failed: {e}")
                self.widget_x.set_status("Move error")

    def _move_y(self, position: float):
        mod = self._get_module(self._actuator_y)
        if mod:
            try:
                self._call_move(mod, position)
                self.widget_y.set_status(f"→ {position:.4f}")
                self.statusbar.showMessage(f"Y moving to {position:.4f}")
            except Exception as e:
                logger.warning(f"Move Y failed: {e}")
                self.widget_y.set_status("Move error")

    def _step_x(self, delta: float):
        new_pos = self.widget_x.current_base + delta
        self.widget_x.target_spinbox.setValue(new_pos / self.widget_x._unit_multiplier)
        self._move_x(new_pos)

    def _step_y(self, delta: float):
        new_pos = self.widget_y.current_base + delta
        self.widget_y.target_spinbox.setValue(new_pos / self.widget_y._unit_multiplier)
        self._move_y(new_pos)

    def _stop_x(self):
        mod = self._get_module(self._actuator_x)
        if mod:
            self._call_stop(mod)
            self.widget_x.set_status("Stopped")

    def _stop_y(self):
        mod = self._get_module(self._actuator_y)
        if mod:
            self._call_stop(mod)
            self.widget_y.set_status("Stopped")

    def _stop_all(self):
        self._stop_x(); self._stop_y()
        self.statusbar.showMessage("All actuators stopped")

    def _go_to_saved(self, x_base: float, y_base: float):
        self._move_x(x_base)
        self._move_y(y_base)
        self.statusbar.showMessage(f"Going to saved position ({x_base:.4g}, {y_base:.4g})")

    # ------------------------------------------------------------------
    # Map helpers
    # ------------------------------------------------------------------
    def _update_map(self):
        self.stage_map.update_position(
            self.widget_x.current_base,
            self.widget_y.current_base)

    def _refresh_map_saved(self):
        self.stage_map.update_saved_positions(self.saved_pos_widget._positions)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------
def main():
    from pymodaq.utils.gui_utils.utils import mkQApp
    from pymodaq.utils.messenger import messagebox

    app = mkQApp(EXTENSION_NAME)
    try:
        from pymodaq_utils.config import Config as PluginConfig
        plugin_config = PluginConfig()
        preset_file_name = plugin_config('presets', f'preset_for_{CLASS_NAME.lower()}')
    except ConfigError:
        messagebox(severity='warning', title='Missing config entry',
                   text=(f'Add to plugin config TOML:\n[presets]\n'
                         f'preset_for_{CLASS_NAME.lower()} = "your_preset_name"'))
        return

    try:
        from pymodaq.dashboard import DashBoard
        from pymodaq_gui.utils import DockArea
        dockarea = DockArea()
        dashboard = DashBoard(dockarea)
        dashboard.set_preset_mode(preset_file_name)
        dockarea.show()
        app.exec()
    except Exception as e:
        logger.exception(e)
        messagebox(severity='critical', title='Dashboard startup error', text=str(e))


if __name__ == '__main__':
    main()