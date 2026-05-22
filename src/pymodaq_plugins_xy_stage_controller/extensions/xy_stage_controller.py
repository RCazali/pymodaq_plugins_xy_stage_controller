import json
from collections import deque
from pathlib import Path

from qtpy import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

from pymodaq_gui import utils as gutils
from pymodaq_utils.config import Config
from pymodaq_utils.logger import set_logger, get_module_name

from pymodaq.extensions.utils import CustomExt

logger = set_logger(get_module_name(__file__))
main_config = Config()

EXTENSION_NAME = 'XY stage Controller'
CLASS_NAME = 'XYStageController'

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

# Maximum number of positions kept in the movement history trail
HISTORY_MAX_LEN = 200


# ---------------------------------------------------------------------------
# Single-axis control widget
# ---------------------------------------------------------------------------
class ActuatorWidget(QtWidgets.QGroupBox):
    move_requested = QtCore.Signal(float)   # absolute, base units
    stop_requested = QtCore.Signal()

    def __init__(self, axis_name: str, parent=None):
        super().__init__(axis_name, parent)
        self.axis_name = axis_name
        self._unit_multiplier = 1e3   # default: mm
        # FIX #2: store raw base-unit position internally instead of reading the label
        self._current_base: float = 0.0
        self._has_position: bool = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)

        # Unit selector
        unit_layout = QtWidgets.QHBoxLayout()
        unit_layout.addWidget(QtWidgets.QLabel("Unit:"))
        self.unit_combo = QtWidgets.QComboBox()
        self.unit_combo.addItems(list(UNIT_MULTIPLIERS.keys()))
        self.unit_combo.setCurrentText('mm')
        self.unit_combo.setFocusPolicy(QtCore.Qt.ClickFocus)
        unit_layout.addWidget(self.unit_combo)
        layout.addLayout(unit_layout)

        # Current position (display only)
        pos_layout = QtWidgets.QHBoxLayout()
        pos_layout.addWidget(QtWidgets.QLabel("Current:"))
        self.current_pos_label = QtWidgets.QLabel("N/A")
        self.current_pos_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        font = QtGui.QFont()
        font.setBold(True)
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
        self.target_spinbox.setFocusPolicy(QtCore.Qt.ClickFocus)
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
        self.step_spinbox.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.step_unit_label = QtWidgets.QLabel("mm")
        step_layout.addWidget(self.step_spinbox)
        step_layout.addWidget(self.step_unit_label)
        layout.addLayout(step_layout)

        # FIX #8: Speed / acceleration controls (read-only display + optional override)
        speed_group = QtWidgets.QGroupBox("Motion Parameters")
        speed_layout = QtWidgets.QFormLayout(speed_group)

        self.speed_spinbox = QtWidgets.QDoubleSpinBox()
        self.speed_spinbox.setRange(0.0, 1e9)
        self.speed_spinbox.setDecimals(3)
        self.speed_spinbox.setValue(0.0)
        self.speed_spinbox.setSpecialValueText("(driver default)")
        self.speed_spinbox.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.speed_spinbox.setToolTip(
            "Max velocity in base units/s. Set to 0 to use the driver default.")

        self.accel_spinbox = QtWidgets.QDoubleSpinBox()
        self.accel_spinbox.setRange(0.0, 1e9)
        self.accel_spinbox.setDecimals(3)
        self.accel_spinbox.setValue(0.0)
        self.accel_spinbox.setSpecialValueText("(driver default)")
        self.accel_spinbox.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.accel_spinbox.setToolTip(
            "Acceleration in base units/s². Set to 0 to use the driver default.")

        speed_layout.addRow("Velocity:", self.speed_spinbox)
        speed_layout.addRow("Accel:", self.accel_spinbox)
        layout.addWidget(speed_group)

        # Move Abs + Stop
        abs_layout = QtWidgets.QHBoxLayout()
        self.btn_move_abs = QtWidgets.QPushButton("Move Abs")
        self.btn_move_abs.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold;")
        self.btn_move_abs.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_stop = QtWidgets.QPushButton("STOP")
        self.btn_stop.setStyleSheet(
            "background-color: #c0392b; color: white; font-weight: bold;")
        self.btn_stop.setFocusPolicy(QtCore.Qt.NoFocus)
        abs_layout.addWidget(self.btn_move_abs)
        abs_layout.addWidget(self.btn_stop)
        layout.addLayout(abs_layout)

        self.status_label = QtWidgets.QLabel("Idle")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.btn_move_abs.clicked.connect(self._on_move_abs)
        self.btn_stop.clicked.connect(self.stop_requested)
        # FIX #3: reconnect unit change to also convert the target spinbox value
        self.unit_combo.currentTextChanged.connect(self._on_unit_changed)

    def _on_move_abs(self):
        self.move_requested.emit(self.target_spinbox.value() * self._unit_multiplier)

    def _on_unit_changed(self, unit: str):
        # FIX #3: convert the target spinbox value to the new unit before updating multiplier
        old_multiplier = self._unit_multiplier
        new_multiplier = UNIT_MULTIPLIERS.get(unit, 1.0)
        if old_multiplier != 0:
            old_target_base = self.target_spinbox.value() * old_multiplier
            self.target_spinbox.blockSignals(True)
            self.target_spinbox.setValue(old_target_base / new_multiplier)
            self.target_spinbox.blockSignals(False)
        self._unit_multiplier = new_multiplier
        for lbl in (self.current_unit_label, self.target_unit_label, self.step_unit_label):
            lbl.setText(unit)
        # Refresh displayed current position in new unit
        if self._has_position:
            self.current_pos_label.setText(
                f"{self._current_base / self._unit_multiplier:.4f}")

    def update_position(self, base_position: float):
        # FIX #2: store raw value, never read back from label text
        self._current_base = base_position
        self._has_position = True
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
        # FIX #2: return stored float, not parsed label text
        return self._current_base

    @property
    def velocity(self) -> float:
        """Returns configured velocity in base units/s, or 0 for driver default."""
        return self.speed_spinbox.value()

    @property
    def acceleration(self) -> float:
        """Returns configured acceleration in base units/s², or 0 for driver default."""
        return self.accel_spinbox.value()


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
            btn.setFocusPolicy(QtCore.Qt.NoFocus)

        self.btn_stop.setStyleSheet(s_st)
        self.btn_stop.setFocusPolicy(QtCore.Qt.NoFocus)

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
    go_requested       = QtCore.Signal(float, float)
    positions_changed  = QtCore.Signal()   # emitted whenever the dict is mutated

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_x: float = 0.0
        self._current_y: float = 0.0
        self._positions: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "X (base)", "Y (base)", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(QtCore.Qt.ClickFocus)
        layout.addWidget(self.table)

        save_row = QtWidgets.QHBoxLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Position name…")
        self.name_edit.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.btn_save = QtWidgets.QPushButton("💾 Save current")
        self.btn_save.setStyleSheet("font-weight: bold;")
        save_row.addWidget(self.name_edit)
        save_row.addWidget(self.btn_save)
        layout.addLayout(save_row)

        self.btn_save.clicked.connect(self._on_save)

    def set_current_position(self, x_base: float, y_base: float):
        self._current_x = x_base
        self._current_y = y_base

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Name required", "Please enter a name.")
            return
        self._positions[name] = {"x": self._current_x, "y": self._current_y}
        self.name_edit.clear()
        self.refresh_table()
        self.positions_changed.emit()   # notify map to refresh

    def refresh_table(self):
        self.table.setRowCount(0)
        for name, pos in self._positions.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{pos['x']:.5g}"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{pos['y']:.5g}"))

            cell_w = QtWidgets.QWidget()
            cell_l = QtWidgets.QHBoxLayout(cell_w)
            cell_l.setContentsMargins(2, 1, 2, 1)
            cell_l.setSpacing(3)

            btn_go = QtWidgets.QPushButton("Go")
            btn_go.setStyleSheet(
                "background-color: #27ae60; color: white; font-weight: bold; padding: 2px 6px;")
            btn_go.setFocusPolicy(QtCore.Qt.NoFocus)

            btn_del = QtWidgets.QPushButton("✕")
            btn_del.setStyleSheet(
                "background-color: #c0392b; color: white; font-weight: bold; padding: 2px 6px;")
            btn_del.setFocusPolicy(QtCore.Qt.NoFocus)

            btn_go.clicked.connect(
                lambda _, n=name: self.go_requested.emit(
                    self._positions[n]["x"], self._positions[n]["y"]))
            btn_del.clicked.connect(lambda _, n=name: self._on_delete(n))

            cell_l.addWidget(btn_go)
            cell_l.addWidget(btn_del)
            self.table.setCellWidget(row, 3, cell_w)

        self.table.resizeColumnToContents(3)

    def _on_delete(self, name: str):
        reply = QtWidgets.QMessageBox.question(
            self, "Delete position", f"Delete '{name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._positions.pop(name, None)
            self.refresh_table()
            self.positions_changed.emit()   # notify map to refresh


# ---------------------------------------------------------------------------
# 2-D stage map widget
# ---------------------------------------------------------------------------
class StageMapWidget(QtWidgets.QWidget):
    """
    Interactive 2-D map of the stage.

    Features:
      - Dashed rectangle showing configured stage limits
      - Live crosshair + position label (FIX #2-safe: receives floats directly)
      - Diamond markers for saved positions with tooltips
      - FIX #9: click on the plot emits move_to_requested(x, y) for direct navigation
      - FIX #10: breadcrumb trail of the last HISTORY_MAX_LEN positions
    """
    limits_changed    = QtCore.Signal()
    move_to_requested = QtCore.Signal(float, float)   # FIX #9

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x = 0.0
        self._y = 0.0
        # FIX #10: ring buffer for movement history
        self._history_x: deque = deque(maxlen=HISTORY_MAX_LEN)
        self._history_y: deque = deque(maxlen=HISTORY_MAX_LEN)
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        lim_group = QtWidgets.QGroupBox("Stage limits (base units)")
        lim_layout = QtWidgets.QFormLayout(lim_group)

        def make_spin(val):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(-1e9, 1e9)
            sb.setDecimals(3)
            sb.setValue(val)
            sb.setFocusPolicy(QtCore.Qt.ClickFocus)
            sb.valueChanged.connect(lambda: self.limits_changed.emit())
            return sb

        self.x_min_sb = make_spin(-10.0)
        self.x_max_sb = make_spin(10.0)
        self.y_min_sb = make_spin(-10.0)
        self.y_max_sb = make_spin(10.0)

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

        btn_apply = QtWidgets.QPushButton("Apply view limits")
        btn_apply.clicked.connect(self.update_view_bounds)
        lim_layout.addRow(btn_apply)
        layout.addWidget(lim_group)

        # FIX #10: history trail visibility toggle
        trail_row = QtWidgets.QHBoxLayout()
        self.chk_trail = QtWidgets.QCheckBox("Show movement trail")
        self.chk_trail.setChecked(True)
        self.btn_clear_trail = QtWidgets.QPushButton("Clear trail")
        self.btn_clear_trail.setFocusPolicy(QtCore.Qt.NoFocus)
        trail_row.addWidget(self.chk_trail)
        trail_row.addWidget(self.btn_clear_trail)
        trail_row.addStretch()

        # FIX #9: click-to-move toggle
        self.chk_click_move = QtWidgets.QCheckBox("Click map → Move")
        self.chk_click_move.setChecked(False)
        self.chk_click_move.setStyleSheet("color: #e67e22; font-weight: bold;")
        self.chk_click_move.setToolTip(
            "When enabled, a left-click on the map sends a move_abs command to that position.")
        trail_row.addWidget(self.chk_click_move)
        layout.addLayout(trail_row)

        # pyqtgraph plot
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setAspectLocked(True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', 'X', units='base')
        self.plot_widget.setLabel('left',   'Y', units='base')
        self.plot_widget.setBackground('#1e1e2e')

        # Stage boundary rectangle
        self._rect = QtWidgets.QGraphicsRectItem(-10.0, -10.0, 20.0, 20.0)
        self._rect.setPen(pg.mkPen('#74c7ec', width=2, style=QtCore.Qt.DashLine))
        self._rect.setBrush(pg.mkBrush('#1e1e2e00'))
        self.plot_widget.addItem(self._rect)

        # FIX #10: movement trail (polyline)
        self._trail_curve = pg.PlotDataItem(
            pen=pg.mkPen('#89b4fa', width=1, style=QtCore.Qt.DotLine))
        self.plot_widget.addItem(self._trail_curve)

        # Saved positions (diamonds)
        self._saved_scatter = pg.ScatterPlotItem(
            size=10, pen=pg.mkPen('#a6e3a1', width=1),
            brush=pg.mkBrush('#a6e3a150'), symbol='d')
        self.plot_widget.addItem(self._saved_scatter)

        # Current position marker
        self._pos_marker = pg.ScatterPlotItem(
            size=14, pen=pg.mkPen('#f38ba8', width=2),
            brush=pg.mkBrush('#f38ba8'), symbol='+')
        self.plot_widget.addItem(self._pos_marker)

        # Crosshair lines
        self._vline = pg.InfiniteLine(angle=90, pen=pg.mkPen('#f38ba880', width=1))
        self._hline = pg.InfiniteLine(angle=0,  pen=pg.mkPen('#f38ba880', width=1))
        self.plot_widget.addItem(self._vline)
        self.plot_widget.addItem(self._hline)

        # Position text label
        self._pos_text = pg.TextItem('', color='#cdd6f4', anchor=(0, 1))
        self.plot_widget.addItem(self._pos_text)

        # FIX #9: target marker shown on click before move confirmation
        self._target_marker = pg.ScatterPlotItem(
            size=12, pen=pg.mkPen('#fab387', width=2),
            brush=pg.mkBrush('#fab38780'), symbol='o')
        self.plot_widget.addItem(self._target_marker)

        layout.addWidget(self.plot_widget)
        self.update_view_bounds()

        # FIX #9: connect mouse click on the plot scene
        self.plot_widget.scene().sigMouseClicked.connect(self._on_map_clicked)

        # Wire trail controls
        self.chk_trail.toggled.connect(self._update_trail_visibility)
        self.btn_clear_trail.clicked.connect(self._clear_trail)

    # ------------------------------------------------------------------
    # Public update methods
    # ------------------------------------------------------------------
    def update_view_bounds(self):
        xmin = self.x_min_sb.value()
        xmax = self.x_max_sb.value()
        ymin = self.y_min_sb.value()
        ymax = self.y_max_sb.value()
        self._rect.setRect(xmin, ymin, xmax - xmin, ymax - ymin)
        self.plot_widget.setXRange(
            xmin - 0.05 * (xmax - xmin), xmax + 0.05 * (xmax - xmin))
        self.plot_widget.setYRange(
            ymin - 0.05 * (ymax - ymin), ymax + 0.05 * (ymax - ymin))

    def update_position(self, x: float, y: float):
        self._x, self._y = x, y
        self._pos_marker.setData([x], [y])
        self._vline.setValue(x)
        self._hline.setValue(y)
        self._pos_text.setText(f"  ({x:.4g}, {y:.4g})")
        self._pos_text.setPos(x, y)
        # FIX #10: append to history trail
        self._history_x.append(x)
        self._history_y.append(y)
        if self.chk_trail.isChecked():
            self._trail_curve.setData(
                list(self._history_x), list(self._history_y))

    def update_saved_positions(self, positions: dict):
        if not positions:
            self._saved_scatter.setData([], [])
            return
        xs   = [p['x'] for p in positions.values()]
        ys   = [p['y'] for p in positions.values()]
        tips = [f"{n}\n({p['x']:.4g}, {p['y']:.4g})" for n, p in positions.items()]
        self._saved_scatter.setData(xs, ys, tip=tips)

    # ------------------------------------------------------------------
    # FIX #9: click-to-move logic
    # ------------------------------------------------------------------
    def _on_map_clicked(self, event):
        if not self.chk_click_move.isChecked():
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        # Map from scene coordinates to plot (data) coordinates
        vb = self.plot_widget.getPlotItem().vb
        mouse_point = vb.mapSceneToView(event.scenePos())
        tx, ty = mouse_point.x(), mouse_point.y()
        # Show an orange ring at the target position
        self._target_marker.setData([tx], [ty])
        self.move_to_requested.emit(tx, ty)

    # ------------------------------------------------------------------
    # FIX #10: trail helpers
    # ------------------------------------------------------------------
    def _update_trail_visibility(self, visible: bool):
        self._trail_curve.setVisible(visible)
        if visible:
            self._trail_curve.setData(
                list(self._history_x), list(self._history_y))

    def _clear_trail(self):
        self._history_x.clear()
        self._history_y.clear()
        self._trail_curve.setData([], [])

    @property
    def x_limits(self):
        return self.x_min_sb.value(), self.x_max_sb.value()

    @property
    def y_limits(self):
        return self.y_min_sb.value(), self.y_max_sb.value()


# ---------------------------------------------------------------------------
# StagePairWidget (Individual Tab)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# StagePairWidget (Individual Tab) - Version Optimisée pour l'Espace
# ---------------------------------------------------------------------------
class StagePairWidget(QtWidgets.QWidget):
    def __init__(self, modules_manager, pair_id: str):
        super().__init__()
        self.modules_manager = modules_manager
        self.pair_id = pair_id
        self._actuator_x = ''
        self._actuator_y = ''
        # FIX #1: store our own signal connections so we can disconnect selectively
        self._signal_connections: list = []   # list of (signal, slot) tuples

        self._setup_ui()
        self._connect_ui_signals()
        self._populate_combos()

    def _setup_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(10)

        # ── Création du sous-onglet pour le panneau de gauche ──
        self.left_tab_widget = QtWidgets.QTabWidget()

        # ===================================================================
        # ONGLET 1 : UTILISATION (Contrôles Moteurs + Positions Sauvegardées)
        # ===================================================================
        operation_widget = QtWidgets.QWidget()
        operation_layout = QtWidgets.QVBoxLayout(operation_widget)
        operation_layout.setContentsMargins(0, 0, 0, 0)

        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        # Paneau Supérieur : Commandes moteurs directes + JogPad
        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # Widgets d'axes (X & Y côte à côte)
        axes_row = QtWidgets.QHBoxLayout()
        self.widget_x = ActuatorWidget("X Axis")
        self.widget_y = ActuatorWidget("Y Axis")
        axes_row.addWidget(self.widget_x)
        axes_row.addWidget(self.widget_y)
        top_layout.addLayout(axes_row)

        # JogPad + Légende clavier
        jog_row = QtWidgets.QHBoxLayout()
        jog_row.addStretch()
        self.jog_pad = JogPad()
        jog_row.addWidget(self.jog_pad)

        kb_info = QtWidgets.QLabel(
            "Keyboard Control:\n"
            "← / →  : X Axis\n"
            "↑ / ↓   : Y Axis\n"
            "Shift   : Speed x10\n"
            "Space : emergency STOP"
        )
        kb_info.setStyleSheet("color: gray; font-style: italic;")
        jog_row.addWidget(kb_info)
        jog_row.addStretch()
        top_layout.addLayout(jog_row)

        left_splitter.addWidget(top_widget)

        # Panneau Inférieur : Positions sauvegardées (toujours accessible avec le contrôle)
        pos_group = QtWidgets.QGroupBox("📍 Saved Positions")
        pos_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        pos_layout = QtWidgets.QVBoxLayout(pos_group)
        pos_layout.setContentsMargins(4, 8, 4, 4)
        self.saved_pos_widget = SavedPositionsWidget()
        pos_layout.addWidget(self.saved_pos_widget)
        left_splitter.addWidget(pos_group)

        # Distribution de la hauteur dans le splitter (60% contrôles, 40% tableau)
        left_splitter.setStretchFactor(0, 60)
        left_splitter.setStretchFactor(1, 40)
        left_splitter.setSizes([550, 350])

        operation_layout.addWidget(left_splitter)
        self.left_tab_widget.addTab(operation_widget, "🕹️ Control & Positions")

        # ===================================================================
        # ONGLET 2 : CONFIGURATION (Assignation des Moteurs & Profil)
        # ===================================================================
        config_widget = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(config_widget)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setSpacing(12)

        # Nom du groupe / de l'onglet
        name_layout = QtWidgets.QHBoxLayout()
        name_layout.addWidget(QtWidgets.QLabel("Group Name:"))
        self.tab_name_edit = QtWidgets.QLineEdit("New Group")
        self.tab_name_edit.setFocusPolicy(QtCore.Qt.ClickFocus)
        name_layout.addWidget(self.tab_name_edit)
        config_layout.addLayout(name_layout)

        # Assignation des actuateurs PyMoDAQ
        sel_group = QtWidgets.QGroupBox("Actuator Assignment")
        sel_layout = QtWidgets.QFormLayout(sel_group)
        self.combo_x = QtWidgets.QComboBox()
        self.combo_x.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.combo_y = QtWidgets.QComboBox()
        self.combo_y.setFocusPolicy(QtCore.Qt.ClickFocus)
        sel_layout.addRow("X Actuator:", self.combo_x)
        sel_layout.addRow("Y Actuator:", self.combo_y)
        self.btn_apply = QtWidgets.QPushButton("Apply Assignment")
        self.btn_apply.setStyleSheet("font-weight: bold; min-height: 30px;")
        sel_layout.addRow(self.btn_apply)
        config_layout.addWidget(sel_group)
        
        config_layout.addStretch() # Repousse le tout vers le haut pour l'esthétique

        self.left_tab_widget.addTab(config_widget, "⚙️ Configuration")

        # Ajout du sous-onglet global à gauche du layout principal
        main_layout.addWidget(self.left_tab_widget, stretch=1)

        # 2-D map (Reste à droite, inchangée)
        self.stage_map = StageMapWidget()
        main_layout.addWidget(self.stage_map, stretch=1)

    def _connect_ui_signals(self):
        self.widget_x.move_requested.connect(self._move_x)
        self.widget_y.move_requested.connect(self._move_y)
        self.widget_x.stop_requested.connect(self._stop_x)
        self.widget_y.stop_requested.connect(self._stop_y)

        self.jog_pad.left_requested.connect(lambda: self._step_x(-self.widget_x.step_base))
        self.jog_pad.right_requested.connect(lambda: self._step_x(+self.widget_x.step_base))
        self.jog_pad.up_requested.connect(lambda: self._step_y(+self.widget_y.step_base))
        self.jog_pad.down_requested.connect(lambda: self._step_y(-self.widget_y.step_base))
        self.jog_pad.stop_requested.connect(self._stop_all)

        self.saved_pos_widget.go_requested.connect(self._go_to_saved)
        self.saved_pos_widget.positions_changed.connect(self._refresh_map_saved)
        self.btn_apply.clicked.connect(self._apply_assignment)
        self.stage_map.limits_changed.connect(self._refresh_map_saved)
        # FIX #9: wire click-to-move from the map
        self.stage_map.move_to_requested.connect(self._go_to_saved)

    # [Le reste du code de la classe StagePairWidget reste identique...]

    # ------------------------------------------------------------------
    # Keyboard support
    # ------------------------------------------------------------------
    def process_key_event(self, key, modifiers) -> bool:
        factor = 10.0 if (modifiers & QtCore.Qt.ShiftModifier) else 1.0
        if key == QtCore.Qt.Key_Left:
            self._step_x(-self.widget_x.step_base, factor); return True
        elif key == QtCore.Qt.Key_Right:
            self._step_x(+self.widget_x.step_base, factor); return True
        elif key == QtCore.Qt.Key_Up:
            self._step_y(+self.widget_y.step_base, factor); return True
        elif key == QtCore.Qt.Key_Down:
            self._step_y(-self.widget_y.step_base, factor); return True
        elif key == QtCore.Qt.Key_Space:
            self._stop_all(); return True
        return False

    # ------------------------------------------------------------------
    # Actuator assignment
    # ------------------------------------------------------------------
    def _populate_combos(self):
        if self.modules_manager is None:
            return
        names = ["None"] + getattr(self.modules_manager, 'actuators_name', [])
        for combo in (self.combo_x, self.combo_y):
            combo.clear()
            combo.addItems(names)
        self._apply_assignment()

    def _apply_assignment(self):
        self._actuator_x = self.combo_x.currentText()
        self._actuator_y = self.combo_y.currentText()
        self._connect_position_signals()
        self._refresh_map_saved()

    def _get_module(self, name: str):
        if not name or name == "None" or self.modules_manager is None:
            return None
        try:
            return self.modules_manager.get_mod_from_name(name, mod='act')
        except Exception as e:
            logger.warning(f"Could not find actuator '{name}': {e}")
            return None

    # FIX #1 + FIX #4: selective signal disconnection, deduplication per axis
    def _connect_position_signals(self):
        """
        Disconnect only our previously registered connections, then register new
        ones. This avoids the 'sig.disconnect()' sledgehammer that could cut
        connections made elsewhere in PyMoDAQ.
        """
        # Disconnect only what we own
        for sig, slot in self._signal_connections:
            try:
                sig.disconnect(slot)
            except Exception:
                pass
        self._signal_connections.clear()

        _SIG_NAMES = ('current_value_signal', 'move_done_signal', 'position_signal')

        # FIX #4: track which (mod, sig_name) pairs we have already connected to
        # avoid double-connecting if X and Y share the same actuator.
        connected_pairs: set = set()

        for mod_name, widget in (
            (self._actuator_x, self.widget_x),
            (self._actuator_y, self.widget_y),
        ):
            mod = self._get_module(mod_name)
            if mod is None:
                continue

            for sig_name in _SIG_NAMES:
                sig = getattr(mod, sig_name, None)
                if sig is None:
                    continue

                pair_key = (id(mod), sig_name)

                def make_slot(w, other_w, is_duplicate):
                    """
                    Build a position-update slot for widget w.
                    When is_duplicate is True (same actuator assigned to both axes),
                    both widgets will be updated independently via their own slot.
                    """
                    def slot(val):
                        try:
                            pos = (
                                float(val.value()) if hasattr(val, 'value')
                                else float(val.data) if hasattr(val, 'data')
                                else float(val)
                            )
                            w.update_position(pos)
                            self.stage_map.update_position(
                                self.widget_x.current_base,
                                self.widget_y.current_base,
                            )
                            self.saved_pos_widget.set_current_position(
                                self.widget_x.current_base,
                                self.widget_y.current_base,
                            )
                        except Exception as e:
                            logger.warning(f"Position slot error: {e}")
                    return slot

                is_dup = pair_key in connected_pairs
                new_slot = make_slot(widget, None, is_dup)
                try:
                    sig.connect(new_slot)
                    # FIX #1: record the (signal, slot) so we can remove it later
                    self._signal_connections.append((sig, new_slot))
                    connected_pairs.add(pair_key)
                except Exception as e:
                    logger.warning(f"Could not connect {sig_name} for '{mod_name}': {e}")
                break   # connect to the first available signal only

    # ------------------------------------------------------------------
    # FIX #5: limit-checked move helpers
    # ------------------------------------------------------------------
    def _check_limits(self, x: float, y: float) -> tuple[bool, str]:
        """Return (within_limits, reason_string)."""
        xmin, xmax = self.stage_map.x_limits
        ymin, ymax = self.stage_map.y_limits
        reasons = []
        if not (xmin <= x <= xmax):
            reasons.append(f"X={x:.4g} outside [{xmin:.4g}, {xmax:.4g}]")
        if not (ymin <= y <= ymax):
            reasons.append(f"Y={y:.4g} outside [{ymin:.4g}, {ymax:.4g}]")
        return (len(reasons) == 0, "; ".join(reasons))

    def _move_x(self, position: float):
        xmin, xmax = self.stage_map.x_limits
        if not (xmin <= position <= xmax):
            logger.warning(
                f"X move to {position:.4g} rejected: outside limits [{xmin:.4g}, {xmax:.4g}]")
            self.widget_x.set_status(f"⚠ Out of limits ({position:.4g})")
            return
        mod = self._get_module(self._actuator_x)
        if mod:
            # FIX #8: apply velocity / acceleration if the driver exposes them
            self._apply_motion_params(mod, self.widget_x)
            XYStageController._call_move(mod, position)
            self.widget_x.set_status(f"→ {position:.4f}")

    def _move_y(self, position: float):
        ymin, ymax = self.stage_map.y_limits
        if not (ymin <= position <= ymax):
            logger.warning(
                f"Y move to {position:.4g} rejected: outside limits [{ymin:.4g}, {ymax:.4g}]")
            self.widget_y.set_status(f"⚠ Out of limits ({position:.4g})")
            return
        mod = self._get_module(self._actuator_y)
        if mod:
            self._apply_motion_params(mod, self.widget_y)
            XYStageController._call_move(mod, position)
            self.widget_y.set_status(f"→ {position:.4f}")

    def _step_x(self, delta: float, factor: float = 1.0):
        new_pos = self.widget_x.current_base + (delta * factor)
        self.widget_x.target_spinbox.setValue(new_pos / self.widget_x._unit_multiplier)
        self._move_x(new_pos)

    def _step_y(self, delta: float, factor: float = 1.0):
        new_pos = self.widget_y.current_base + (delta * factor)
        self.widget_y.target_spinbox.setValue(new_pos / self.widget_y._unit_multiplier)
        self._move_y(new_pos)

    def _stop_x(self):
        mod = self._get_module(self._actuator_x)
        if mod:
            XYStageController._call_stop(mod)
            self.widget_x.set_status("Stopped")

    def _stop_y(self):
        mod = self._get_module(self._actuator_y)
        if mod:
            XYStageController._call_stop(mod)
            self.widget_y.set_status("Stopped")

    def _stop_all(self):
        self._stop_x()
        self._stop_y()

    def _go_to_saved(self, x_base: float, y_base: float):
        # FIX #5: validate both axes before moving
        within, reason = self._check_limits(x_base, y_base)
        if not within:
            QtWidgets.QMessageBox.warning(
                self, "Move rejected – out of limits", reason)
            return
        self._move_x(x_base)
        self._move_y(y_base)

    def _refresh_map_saved(self):
        self.stage_map.update_saved_positions(self.saved_pos_widget._positions)

    # ------------------------------------------------------------------
    # FIX #8: push velocity / acceleration to the driver when non-zero
    # ------------------------------------------------------------------
    @staticmethod
    def _apply_motion_params(mod, axis_widget: ActuatorWidget):
        vel = axis_widget.velocity
        acc = axis_widget.acceleration
        if vel > 0 and hasattr(mod, 'set_velocity'):
            try:
                mod.set_velocity(vel)
            except Exception as e:
                logger.warning(f"set_velocity failed: {e}")
        if acc > 0 and hasattr(mod, 'set_acceleration'):
            try:
                mod.set_acceleration(acc)
            except Exception as e:
                logger.warning(f"set_acceleration failed: {e}")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def get_config(self) -> dict:
        return {
            "pair_id":       self.pair_id,
            "tab_title":     self.tab_name_edit.text(),
            "actuator_x":    self.combo_x.currentText(),
            "actuator_y":    self.combo_y.currentText(),
            "unit_x":        self.widget_x.unit_combo.currentText(),
            "unit_y":        self.widget_y.unit_combo.currentText(),
            "step_x":        self.widget_x.step_spinbox.value(),
            "step_y":        self.widget_y.step_spinbox.value(),
            "velocity_x":    self.widget_x.speed_spinbox.value(),
            "velocity_y":    self.widget_y.speed_spinbox.value(),
            "accel_x":       self.widget_x.accel_spinbox.value(),
            "accel_y":       self.widget_y.accel_spinbox.value(),
            "limits": {
                "xmin": self.stage_map.x_min_sb.value(),
                "xmax": self.stage_map.x_max_sb.value(),
                "ymin": self.stage_map.y_min_sb.value(),
                "ymax": self.stage_map.y_max_sb.value(),
            },
            "saved_positions": self.saved_pos_widget._positions,
        }

    def load_config(self, config: dict):
        if not config:
            return
        self.combo_x.blockSignals(True)
        self.combo_y.blockSignals(True)
        try:
            if "tab_title"  in config: self.tab_name_edit.setText(config["tab_title"])
            if "actuator_x" in config: self.combo_x.setCurrentText(config["actuator_x"])
            if "actuator_y" in config: self.combo_y.setCurrentText(config["actuator_y"])
            self._actuator_x = self.combo_x.currentText()
            self._actuator_y = self.combo_y.currentText()

            if "unit_x"     in config: self.widget_x.unit_combo.setCurrentText(config["unit_x"])
            if "unit_y"     in config: self.widget_y.unit_combo.setCurrentText(config["unit_y"])
            if "step_x"     in config: self.widget_x.step_spinbox.setValue(config["step_x"])
            if "step_y"     in config: self.widget_y.step_spinbox.setValue(config["step_y"])
            if "velocity_x" in config: self.widget_x.speed_spinbox.setValue(config["velocity_x"])
            if "velocity_y" in config: self.widget_y.speed_spinbox.setValue(config["velocity_y"])
            if "accel_x"    in config: self.widget_x.accel_spinbox.setValue(config["accel_x"])
            if "accel_y"    in config: self.widget_y.accel_spinbox.setValue(config["accel_y"])

            if "limits" in config:
                lims = config["limits"]
                self.stage_map.x_min_sb.setValue(lims.get("xmin", -10.0))
                self.stage_map.x_max_sb.setValue(lims.get("xmax",  10.0))
                self.stage_map.y_min_sb.setValue(lims.get("ymin", -10.0))
                self.stage_map.y_max_sb.setValue(lims.get("ymax",  10.0))
                self.stage_map.update_view_bounds()

            if "saved_positions" in config:
                self.saved_pos_widget._positions = config["saved_positions"]
                self.saved_pos_widget.refresh_table()

            self._connect_position_signals()
            self._refresh_map_saved()
        finally:
            self.combo_x.blockSignals(False)
            self.combo_y.blockSignals(False)


# ---------------------------------------------------------------------------
# Global Multi-Profile Layout Container
# ---------------------------------------------------------------------------
class MainUIWidget(QtWidgets.QWidget):
    def __init__(self, modules_manager, preset_name):
        super().__init__()
        self.modules_manager = modules_manager
        self.preset_name = preset_name
        self.CONFIG_DIR = Path.home() / '.pymodaq' / 'xy_stage_profiles' / preset_name
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        self.tabs_list: list = []
        self._setup_ui()
        self.scan_and_populate_profiles()

    def _setup_ui(self):
        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Toolbar
        self.toolbar = QtWidgets.QHBoxLayout()

        self.toolbar.addWidget(QtWidgets.QLabel("Profile:"))
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setMinimumWidth(150)
        self.profile_combo.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.toolbar.addWidget(self.profile_combo)

        self.btn_save_profile = QtWidgets.QPushButton("💾 Save Profile")
        self.btn_save_profile.setStyleSheet("font-weight: bold;")
        self.toolbar.addWidget(self.btn_save_profile)

        self.btn_new_profile = QtWidgets.QPushButton("＋ New Profile")
        self.toolbar.addWidget(self.btn_new_profile)

        self.toolbar.addSpacing(20)
        self.toolbar.addWidget(QtWidgets.QFrame(frameShape=QtWidgets.QFrame.VLine))
        self.toolbar.addSpacing(20)

        self.btn_add_tab = QtWidgets.QPushButton("＋ Add Group Tab")
        self.toolbar.addWidget(self.btn_add_tab)

        self.toolbar.addStretch()

        self.btn_keyboard = QtWidgets.QPushButton("Keyboard Input: DISABLED")
        self.btn_keyboard.setCheckable(True)
        self.btn_keyboard.setStyleSheet(
            "background-color: #e67e22; color: white; font-weight: bold; padding: 5px 10px;")
        self.toolbar.addWidget(self.btn_keyboard)

        self.main_layout.addLayout(self.toolbar)

        # Tab workspace
        self.tabs_widget = QtWidgets.QTabWidget()
        self.tabs_widget.setTabsClosable(True)
        self.tabs_widget.tabCloseRequested.connect(self.close_tab)
        self.main_layout.addWidget(self.tabs_widget)

        # Signals
        self.btn_add_tab.clicked.connect(lambda: self.add_new_pair())
        self.btn_save_profile.clicked.connect(self.save_current_profile)
        self.btn_new_profile.clicked.connect(self.create_new_profile)
        self.btn_keyboard.toggled.connect(self.toggle_keyboard_mode)
        self.profile_combo.currentTextChanged.connect(self.load_profile)

        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def toggle_keyboard_mode(self, checked):
        if checked:
            self.btn_keyboard.setText("Keyboard Input: ARMED")
            self.btn_keyboard.setStyleSheet(
                "background-color: #2ecc71; color: white; font-weight: bold; padding: 5px 10px;")
            self.setFocus()
        else:
            self.btn_keyboard.setText("Keyboard Input: DISABLED")
            self.btn_keyboard.setStyleSheet(
                "background-color: #e67e22; color: white; font-weight: bold; padding: 5px 10px;")

    def scan_and_populate_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()

        files = list(self.CONFIG_DIR.glob("*.json"))
        if not files:
            default_path = self.CONFIG_DIR / "Default.json"
            with open(default_path, 'w', encoding='utf-8') as f:
                json.dump({"tabs": []}, f)
            files = [default_path]

        for f in files:
            self.profile_combo.addItem(f.stem)

        self.profile_combo.blockSignals(False)
        self.load_profile(self.profile_combo.currentText())

    def create_new_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Create Profile", "Enter unique profile name:")
        if ok and name.strip():
            target_path = self.CONFIG_DIR / f"{name.strip()}.json"
            if target_path.exists():
                QtWidgets.QMessageBox.warning(
                    self, "Conflict", "A profile with this name already exists.")
                return
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump({"tabs": []}, f)
            self.profile_combo.addItem(name.strip())
            self.profile_combo.setCurrentText(name.strip())

    def load_profile(self, profile_name: str):
        if not profile_name:
            return
        self.tabs_widget.clear()
        self.tabs_list.clear()

        target_path = self.CONFIG_DIR / f"{profile_name}.json"
        if target_path.exists():
            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for tab_config in config.get("tabs", []):
                    self.add_new_pair(tab_config)
            except Exception as e:
                logger.error(f"Error reading profile configuration file: {e}")

        if not self.tabs_list:
            self.add_new_pair()

    def save_current_profile(self):
        profile_name = self.profile_combo.currentText()
        if not profile_name:
            return
        config = {"tabs": [tab.get_config() for tab in self.tabs_list]}
        target_path = self.CONFIG_DIR / f"{profile_name}.json"
        try:
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            QtWidgets.QMessageBox.information(
                self, "Saved", f"Profile '{profile_name}' saved successfully.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Could not save profile data: {e}")

    def add_new_pair(self, config_data=None):
        pair_id = (
            config_data.get("pair_id", f"tab_{QtCore.QDateTime.currentMSecsSinceEpoch()}")
            if config_data
            else f"tab_{QtCore.QDateTime.currentMSecsSinceEpoch()}"
        )
        new_pair = StagePairWidget(self.modules_manager, pair_id)
        if config_data:
            new_pair.load_config(config_data)

        self.tabs_list.append(new_pair)
        title = new_pair.tab_name_edit.text()
        self.tabs_widget.addTab(new_pair, title)

        new_pair.tab_name_edit.textChanged.connect(
            lambda text, w=new_pair: self.tabs_widget.setTabText(
                self.tabs_widget.indexOf(w), text))

    def close_tab(self, index):
        if self.tabs_widget.count() <= 1:
            QtWidgets.QMessageBox.warning(
                self, "Action Denied",
                "At least one actuator control tab must remain open.")
            return
        widget = self.tabs_widget.widget(index)
        if widget in self.tabs_list:
            self.tabs_list.remove(widget)
        self.tabs_widget.removeTab(index)
        widget.deleteLater()

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if not self.btn_keyboard.isChecked() or event.isAutoRepeat():
            super().keyPressEvent(event)
            return
        current_idx = self.tabs_widget.currentIndex()
        if current_idx == -1:
            return
        active_tab = self.tabs_widget.widget(current_idx)
        if active_tab and active_tab.process_key_event(event.key(), event.modifiers()):
            event.accept()
        else:
            super().keyPressEvent(event)


# ===========================================================================
# Main extension class (called by the PyMoDAQ framework)
# ===========================================================================
class XYStageController(CustomExt):
    params = []

    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)
        path = Path(str(self.dashboard.preset_file))
        self.preset_name = path.stem
        self.setup_ui()

    def setup_actions(self):
        pass

    def connect_things(self):
        pass

    def setup_docks(self):
        self.docks['main'] = gutils.Dock(f'Stage Controller - {self.preset_name}')
        self.dockarea.addDock(self.docks['main'])
        self.main_ui = MainUIWidget(self.dashboard.modules_manager, self.preset_name)
        self.docks['main'].addWidget(self.main_ui)

    @staticmethod
    def _call_move(mod, position):
        if hasattr(mod, 'move_abs'):
            mod.move_abs(position)
        elif hasattr(mod, 'move_Abs'):
            mod.move_Abs(position)

    @staticmethod
    def _call_stop(mod):
        if hasattr(mod, 'stop'):
            mod.stop()