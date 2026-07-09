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

HISTORY_MAX_LEN = 2000


# ---------------------------------------------------------------------------
# Single-axis control widget (unit-agnostic)
# ---------------------------------------------------------------------------
class ActuatorWidget(QtWidgets.QGroupBox):
    move_requested = QtCore.Signal(float)   # Absolute target position in the actuator's native unit
    stop_requested = QtCore.Signal()

    def __init__(self, axis_name: str, parent=None):
        super().__init__(parent)
        self.setTitle(axis_name)
        self.axis_name = axis_name
        self._current_raw: float = 0.0
        self._has_position: bool = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)

        pos_layout = QtWidgets.QHBoxLayout()
        pos_layout.addWidget(QtWidgets.QLabel("Current:"))
        self.current_pos_label = QtWidgets.QLabel("N/A")
        self.current_pos_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        font = QtGui.QFont()
        font.setBold(True)
        self.current_pos_label.setFont(font)
        self.current_unit_label = QtWidgets.QLabel("u.a.")
        pos_layout.addWidget(self.current_pos_label)
        pos_layout.addWidget(self.current_unit_label)
        layout.addLayout(pos_layout)

        target_layout = QtWidgets.QHBoxLayout()
        target_layout.addWidget(QtWidgets.QLabel("Target:"))
        self.target_spinbox = QtWidgets.QDoubleSpinBox()
        self.target_spinbox.setRange(-1e9, 1e9)
        self.target_spinbox.setDecimals(3)
        self.target_spinbox.setSingleStep(1)
        self.target_spinbox.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.target_unit_label = QtWidgets.QLabel("u.a.")
        target_layout.addWidget(self.target_spinbox)
        target_layout.addWidget(self.target_unit_label)
        layout.addLayout(target_layout)

        step_layout = QtWidgets.QHBoxLayout()
        step_layout.addWidget(QtWidgets.QLabel("Step:"))
        self.step_spinbox = QtWidgets.QDoubleSpinBox()
        self.step_spinbox.setRange(1e-9, 1e9)
        self.step_spinbox.setDecimals(3)
        self.step_spinbox.setSingleStep(1)
        self.step_spinbox.setValue(1.0)
        self.step_spinbox.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.step_unit_label = QtWidgets.QLabel("u.a.")
        step_layout.addWidget(self.step_spinbox)
        step_layout.addWidget(self.step_unit_label)
        layout.addLayout(step_layout)

        abs_layout = QtWidgets.QHBoxLayout()
        self.btn_move_abs = QtWidgets.QPushButton("Move Abs")
        self.btn_move_abs.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold;")
        self.btn_move_abs.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_stop = QtWidgets.QPushButton("STOP")
        self.btn_stop.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self.btn_stop.setFocusPolicy(QtCore.Qt.NoFocus)
        abs_layout.addWidget(self.btn_move_abs)
        abs_layout.addWidget(self.btn_stop)
        layout.addLayout(abs_layout)

        self.status_label = QtWidgets.QLabel("Idle")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.btn_move_abs.clicked.connect(self._on_move_abs)
        self.btn_stop.clicked.connect(self.stop_requested)

    def _on_move_abs(self):
        self.move_requested.emit(self.target_spinbox.value())

    def update_position(self, raw_position: float):
        self._current_raw = raw_position
        self._has_position = True
        self.current_pos_label.setText(f"{raw_position:.3f}")

    def update_unit_display(self, unit_str: str):
        if not unit_str:
            unit_str = "u.a."
        self.current_unit_label.setText(unit_str)
        self.target_unit_label.setText(unit_str)
        self.step_unit_label.setText(unit_str)

    def set_status(self, text: str):
        self.status_label.setText(text)

    @property
    def step_val(self) -> float:
        return self.step_spinbox.value()

    @property
    def current_raw(self) -> float:
        return self._current_raw

    def set_busy(self, busy: bool):
        if busy:
            self.current_pos_label.setStyleSheet("background-color: #e67e22; color: white; padding: 2px; border-radius: 3px;")
            self.status_label.setText("Moving...")
        else:
            self.current_pos_label.setStyleSheet("")
            self.status_label.setText("Idle")


# ---------------------------------------------------------------------------
# Jog Pad
# ---------------------------------------------------------------------------
class JogPad(QtWidgets.QWidget):
    left_requested  = QtCore.Signal()
    right_requested = QtCore.Signal()
    up_requested    = QtCore.Signal()
    down_requested  = QtCore.Signal()
    home_requested  = QtCore.Signal()
    stop_requested  = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setSpacing(4)

        s_mv = "font-size: 18px; font-weight: bold; min-width: 52px; min-height: 52px;"
        s_hm = s_mv + "background-color: #34495e; color: white; font-size: 14px;"

        self.btn_up    = QtWidgets.QPushButton("▲\nY+")
        self.btn_down  = QtWidgets.QPushButton("▼\nY−")
        self.btn_left  = QtWidgets.QPushButton("◀\nX−")
        self.btn_right = QtWidgets.QPushButton("▶\nX+")
        self.btn_home  = QtWidgets.QPushButton("🏠\nHome")

        for btn in (self.btn_up, self.btn_down, self.btn_left, self.btn_right, self.btn_home):
            btn.setFocusPolicy(QtCore.Qt.NoFocus)
            if btn == self.btn_home:
                btn.setStyleSheet(s_hm)
            else:
                btn.setStyleSheet(s_mv)

        grid.addWidget(self.btn_up,    0, 1)
        grid.addWidget(self.btn_left,  1, 0)
        grid.addWidget(self.btn_home,  1, 1)
        grid.addWidget(self.btn_right, 1, 2)
        grid.addWidget(self.btn_down,  2, 1)

        self.btn_left.clicked.connect(self.left_requested)
        self.btn_right.clicked.connect(self.right_requested)
        self.btn_up.clicked.connect(self.up_requested)
        self.btn_down.clicked.connect(self.down_requested)
        self.btn_home.clicked.connect(self.home_requested)


# ---------------------------------------------------------------------------
# Saved positions panel
# ---------------------------------------------------------------------------
class SavedPositionsWidget(QtWidgets.QWidget):
    go_requested       = QtCore.Signal(float, float)
    positions_changed  = QtCore.Signal()

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
        self.table.setHorizontalHeaderLabels(["Name", "X", "Y", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
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

    def set_current_position(self, x: float, y: float):
        self._current_x = x
        self._current_y = y

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Name required", "Please enter a name.")
            return
        self._positions[name] = {"x": self._current_x, "y": self._current_y}
        self.name_edit.clear()
        self.refresh_table()
        self.positions_changed.emit()

    def refresh_table(self):
        self.table.setRowCount(0)
        for name, pos in self._positions.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{pos['x']:.3f}"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{pos['y']:.3f}"))

            cell_w = QtWidgets.QWidget()
            cell_l = QtWidgets.QHBoxLayout(cell_w)
            cell_l.setContentsMargins(2, 1, 2, 1)
            cell_l.setSpacing(3)

            btn_go = QtWidgets.QPushButton("Go")
            btn_go.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 2px 6px;")
            btn_go.setFocusPolicy(QtCore.Qt.NoFocus)

            btn_del = QtWidgets.QPushButton("✕")
            btn_del.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; padding: 2px 6px;")
            btn_del.setFocusPolicy(QtCore.Qt.NoFocus)

            btn_go.clicked.connect(lambda _, n=name: self.go_requested.emit(self._positions[n]["x"], self._positions[n]["y"]))
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
            self.positions_changed.emit()


# ---------------------------------------------------------------------------
# 2-D stage map widget (coordinates in the actuator's native units)
# ---------------------------------------------------------------------------
class StageMapWidget(QtWidgets.QWidget):
    limits_changed    = QtCore.Signal()
    move_to_requested = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x = 0.0
        self._y = 0.0
        self._history_x: deque = deque(maxlen=HISTORY_MAX_LEN)
        self._history_y: deque = deque(maxlen=HISTORY_MAX_LEN)
        
        self._past_scans_items = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        lim_group = QtWidgets.QGroupBox("Stage limits (Native Units)")
        lim_layout = QtWidgets.QFormLayout(lim_group)

        def make_spin(val):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(-1e9, 1e9)
            sb.setDecimals(3)
            sb.setValue(val)
            sb.setFocusPolicy(QtCore.Qt.ClickFocus)
            sb.valueChanged.connect(lambda: self.limits_changed.emit())
            return sb

        self.x_min_sb = make_spin(-50.0)
        self.x_max_sb = make_spin(50.0)
        self.y_min_sb = make_spin(-50.0)
        self.y_max_sb = make_spin(50.0)

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

        trail_row = QtWidgets.QHBoxLayout()
        self.chk_trail = QtWidgets.QCheckBox("Show trail")
        self.chk_trail.setChecked(True)
        
        self.btn_clear_trail = QtWidgets.QPushButton("Clear trail")
        self.btn_clear_trail.setFocusPolicy(QtCore.Qt.NoFocus)
        
        self.btn_clear_history = QtWidgets.QPushButton("🗑️ Clear Scans History")
        self.btn_clear_history.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_clear_history.setStyleSheet("background-color: #34495e; color: white;")
        
        trail_row.addWidget(self.chk_trail)
        trail_row.addWidget(self.btn_clear_trail)
        trail_row.addWidget(self.btn_clear_history)
        trail_row.addStretch()

        self.chk_click_move = QtWidgets.QCheckBox("Click map → Move")
        self.chk_click_move.setChecked(False)
        self.chk_click_move.setStyleSheet("color: #e67e22; font-weight: bold;")
        trail_row.addWidget(self.chk_click_move)
        layout.addLayout(trail_row)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setAspectLocked(True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', 'X', units='native')
        self.plot_widget.setLabel('left',   'Y', units='native')
        self.plot_widget.setBackground('#1e1e2e')

        self._rect = QtWidgets.QGraphicsRectItem(-50.0, -50.0, 100.0, 100.0)
        self._rect.setPen(pg.mkPen('#74c7ec', width=2, style=QtCore.Qt.DashLine))
        self.plot_widget.addItem(self._rect)

        self._trail_curve = pg.PlotDataItem(pen=pg.mkPen('#89b4fa', width=1, style=QtCore.Qt.DotLine))
        self.plot_widget.addItem(self._trail_curve)

        self._saved_scatter = pg.ScatterPlotItem(size=10, pen=pg.mkPen('#a6e3a1', width=1), brush=pg.mkBrush('#a6e3a150'), symbol='d')
        self.plot_widget.addItem(self._saved_scatter)

        self._pos_marker = pg.ScatterPlotItem(size=14, pen=pg.mkPen('#f38ba8', width=2), brush=pg.mkBrush('#f38ba8'), symbol='+')
        self.plot_widget.addItem(self._pos_marker)

        self._vline = pg.InfiniteLine(angle=90, pen=pg.mkPen('#f38ba880', width=1))
        self._hline = pg.InfiniteLine(angle=0,  pen=pg.mkPen('#f38ba880', width=1))
        self.plot_widget.addItem(self._vline)
        self.plot_widget.addItem(self._hline)

        self._pos_text = pg.TextItem('', color='#cdd6f4', anchor=(0, 1))
        self.plot_widget.addItem(self._pos_text)

        self._target_marker = pg.ScatterPlotItem(size=12, pen=pg.mkPen('#fab387', width=2), brush=pg.mkBrush('#fab38780'), symbol='o')
        self.plot_widget.addItem(self._target_marker)

        layout.addWidget(self.plot_widget)
        self.update_view_bounds()
        self.plot_widget.scene().sigMouseClicked.connect(self._on_map_clicked)
        self.chk_trail.toggled.connect(self._update_trail_visibility)
        self.btn_clear_trail.clicked.connect(self._clear_trail)
        self.btn_clear_history.clicked.connect(self.clear_scans_history)

    def set_labels_units(self, unit: str):
        self.plot_widget.setLabel('bottom', 'X', units=unit)
        self.plot_widget.setLabel('left',   'Y', units=unit)

    def add_scan_zone_to_history(self, xmin: float, xmax: float, dx: float, ymin: float, ymax: float, dy: float, completed: bool = True):
        color_hex = '#2ecc71' if completed else '#e67e22'
        rect_item = QtWidgets.QGraphicsRectItem(xmin, ymin, xmax - xmin, ymax - ymin)
        rect_item.setPen(pg.mkPen(color_hex, width=1.5, style=QtCore.Qt.SolidLine))
        rect_item.setBrush(pg.mkBrush(color_hex + '15')) 
        
        label_str = f"dx:{dx:.3g}\ndy:{dy:.3g}"
        text_item = pg.TextItem(label_str, color='#a6adc8', anchor=(0.5, 0.5))
        text_item.setPos((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
        
        self.plot_widget.addItem(rect_item)
        self.plot_widget.addItem(text_item)
        
        self._past_scans_items.append(rect_item)
        self._past_scans_items.append(text_item)

    def clear_scans_history(self):
        for item in self._past_scans_items:
            self.plot_widget.removeItem(item)
        self._past_scans_items.clear()

    def update_view_bounds(self):
        xmin = self.x_min_sb.value()
        xmax = self.x_max_sb.value()
        ymin = self.y_min_sb.value()
        ymax = self.y_max_sb.value()
        self._rect.setRect(xmin, ymin, xmax - xmin, ymax - ymin)
        self.plot_widget.setXRange(xmin - 0.05 * (xmax - xmin), xmax + 0.05 * (xmax - xmin))
        self.plot_widget.setYRange(ymin - 0.05 * (ymax - ymin), ymax + 0.05 * (ymax - ymin))

    def update_position(self, x: float, y: float):
        self._x, self._y = x, y
        self._pos_marker.setData([x], [y])
        self._vline.setValue(x)
        self._hline.setValue(y)
        self._pos_text.setText(f"  ({x:.2f}, {y:.2f})")
        self._pos_text.setPos(x, y)
        self._history_x.append(x)
        self._history_y.append(y)
        if self.chk_trail.isChecked():
            self._trail_curve.setData(list(self._history_x), list(self._history_y))

    def update_saved_positions(self, positions: dict):
        if not positions:
            self._saved_scatter.setData([], [])
            return
        xs   = [p['x'] for p in positions.values()]
        ys   = [p['y'] for p in positions.values()]
        tips = [f"{n}\n({p['x']:.3g}, {p['y']:.3g})" for n, p in positions.items()]
        self._saved_scatter.setData(xs, ys, tip=tips)

    def _on_map_clicked(self, event):
        if not self.chk_click_move.isChecked():
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        vb = self.plot_widget.getPlotItem().vb
        mouse_point = vb.mapSceneToView(event.scenePos())
        tx, ty = mouse_point.x(), mouse_point.y()
        self._target_marker.setData([tx], [ty])
        self.move_to_requested.emit(tx, ty)

    def _update_trail_visibility(self, visible: bool):
        self._trail_curve.setVisible(visible)
        if visible:
            self._trail_curve.setData(list(self._history_x), list(self._history_y))

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
# StagePairWidget — event-driven XY pair controller
# ---------------------------------------------------------------------------
class StagePairWidget(QtWidgets.QWidget):
    def __init__(self, modules_manager, pair_id: str):
        super().__init__()
        self.modules_manager = modules_manager
        self.pair_id = pair_id
        self._actuator_x = ''
        self._actuator_y = ''
        self._signal_connections: list = []

        self._scan_running: bool = False
        self._scan_pattern: list = []
        self._scan_index: int = 0
        
        # Per-axis move-done flags used by the event-driven scan engine
        self._x_ready: bool = False
        self._y_ready: bool = False
        
        self._current_scan_bounds: dict = {}
        self._setup_ui()
        self._connect_ui_signals()
        self._populate_combos()

    def _setup_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(10)

        self.left_tab_widget = QtWidgets.QTabWidget()
        operation_widget = QtWidgets.QWidget()
        operation_layout = QtWidgets.QVBoxLayout(operation_widget)
        operation_layout.setContentsMargins(0, 0, 0, 0)

        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        axes_row = QtWidgets.QHBoxLayout()
        self.widget_x = ActuatorWidget("X Axis")
        self.widget_y = ActuatorWidget("Y Axis")
        axes_row.addWidget(self.widget_x)
        axes_row.addWidget(self.widget_y)
        top_layout.addLayout(axes_row)

        jog_row = QtWidgets.QHBoxLayout()
        jog_row.addStretch()
        self.jog_pad = JogPad()
        jog_row.addWidget(self.jog_pad)

        kb_info = QtWidgets.QLabel(
            "Keyboard Control:\n"
            "← / →  : X Axis\n"
            "↑ / ↓   : Y Axis\n"
            "+ / -   : Step x10 / /10\n"
            "S       : START/STOP SCAN\n"
            "Space : emergency STOP"
        )
        kb_info.setStyleSheet("color: gray; font-style: italic;")
        jog_row.addWidget(kb_info)
        jog_row.addStretch()
        top_layout.addLayout(jog_row)
        left_splitter.addWidget(top_widget)

        pos_group = QtWidgets.QGroupBox("📍 Saved Positions")
        pos_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        pos_layout = QtWidgets.QVBoxLayout(pos_group)
        pos_layout.setContentsMargins(4, 8, 4, 4)
        self.saved_pos_widget = SavedPositionsWidget()
        pos_layout.addWidget(self.saved_pos_widget)
        left_splitter.addWidget(pos_group)

        left_splitter.setStretchFactor(0, 60)
        left_splitter.setStretchFactor(1, 40)
        left_splitter.setSizes([550, 350])

        operation_layout.addWidget(left_splitter)
        self.left_tab_widget.addTab(operation_widget, "🕹️ Control & Positions")

        config_widget = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(config_widget)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setSpacing(12)

        name_layout = QtWidgets.QHBoxLayout()
        name_layout.addWidget(QtWidgets.QLabel("Group Name:"))
        self.tab_name_edit = QtWidgets.QLineEdit("New Group")
        self.tab_name_edit.setFocusPolicy(QtCore.Qt.ClickFocus)
        name_layout.addWidget(self.tab_name_edit)
        config_layout.addLayout(name_layout)

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
        
        scan_group = QtWidgets.QGroupBox("🐍 Hardware Snake Scan (Event-driven)")
        scan_group.setStyleSheet("QGroupBox { font-weight: bold; color: #2ecc71; }")
        scan_grid = QtWidgets.QGridLayout(scan_group)
        
        def make_scan_spin(val, is_step=False):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(1e-9 if is_step else -1e9, 1e9)
            sb.setDecimals(3)
            sb.setValue(val)
            sb.setFocusPolicy(QtCore.Qt.ClickFocus)
            return sb

        self.scan_x_min = make_scan_spin(-10.0)
        self.scan_x_max = make_scan_spin(10.0)
        self.scan_x_step = make_scan_spin(1.0, is_step=True)
        
        self.scan_y_min = make_scan_spin(-10.0)
        self.scan_y_max = make_scan_spin(10.0)
        self.scan_y_step = make_scan_spin(1.0, is_step=True)
        
        scan_grid.addWidget(QtWidgets.QLabel("X Min:"), 0, 0)
        scan_grid.addWidget(self.scan_x_min, 0, 1)
        scan_grid.addWidget(QtWidgets.QLabel("X Max:"), 0, 2)
        scan_grid.addWidget(self.scan_x_max, 0, 3)
        scan_grid.addWidget(QtWidgets.QLabel("X Step:"), 0, 4)
        scan_grid.addWidget(self.scan_x_step, 0, 5)
        
        scan_grid.addWidget(QtWidgets.QLabel("Y Min:"), 1, 0)
        scan_grid.addWidget(self.scan_y_min, 1, 1)
        scan_grid.addWidget(QtWidgets.QLabel("Y Max:"), 1, 2)
        scan_grid.addWidget(self.scan_y_max, 1, 3)
        scan_grid.addWidget(QtWidgets.QLabel("Y Step:"), 1, 4)
        scan_grid.addWidget(self.scan_y_step, 1, 5)
        
        self.btn_toggle_scan = QtWidgets.QPushButton("🚀 START HARDWARE SCAN (Key: S)")
        self.btn_toggle_scan.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; min-height: 35px;")
        scan_grid.addWidget(self.btn_toggle_scan, 2, 0, 1, 6)
        
        config_layout.addWidget(scan_group)
        config_layout.addStretch()

        self.left_tab_widget.addTab(config_widget, "⚙️ Configuration")
        main_layout.addWidget(self.left_tab_widget, stretch=1)

        self.stage_map = StageMapWidget()
        main_layout.addWidget(self.stage_map, stretch=1)

    def _connect_ui_signals(self):
        self.widget_x.move_requested.connect(self._move_x)
        self.widget_y.move_requested.connect(self._move_y)
        self.widget_x.stop_requested.connect(self._stop_x)
        self.widget_y.stop_requested.connect(self._stop_y)

        self.jog_pad.left_requested.connect(lambda: self._step_x(-self.widget_x.step_val))
        self.jog_pad.right_requested.connect(lambda: self._step_x(+self.widget_x.step_val))
        self.jog_pad.up_requested.connect(lambda: self._step_y(+self.widget_y.step_val))
        self.jog_pad.down_requested.connect(lambda: self._step_y(-self.widget_y.step_val))
        self.jog_pad.stop_requested.connect(self._stop_all)

        self.saved_pos_widget.go_requested.connect(self._go_to_saved)
        self.saved_pos_widget.positions_changed.connect(self._refresh_map_saved)
        self.btn_apply.clicked.connect(self._apply_assignment)
        self.stage_map.limits_changed.connect(self._refresh_map_saved)
        self.stage_map.move_to_requested.connect(self._go_to_saved)
        self.jog_pad.home_requested.connect(lambda: self._go_to_saved(0.0, 0.0))
        
        self.btn_toggle_scan.clicked.connect(self._on_toggle_scan_clicked)

    def _generate_snake_pattern(self) -> list:
        xmin, xmax, step_x = self.scan_x_min.value(), self.scan_x_max.value(), self.scan_x_step.value()
        ymin, ymax, step_y = self.scan_y_min.value(), self.scan_y_max.value(), self.scan_y_step.value()

        if step_x <= 0 or step_y <= 0 or xmin >= xmax or ymin >= ymax:
            return []

        import numpy as np
        x_range = np.arange(xmin, xmax + step_x / 2, step_x)
        y_range = np.arange(ymin, ymax + step_y / 2, step_y)

        pattern = []
        for i, y in enumerate(y_range):
            x_line = reversed(x_range) if i % 2 == 1 else x_range
            for x in x_line:
                pattern.append((float(x), float(y)))
        return pattern

    def _on_toggle_scan_clicked(self):
        if self._scan_running:
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self):
        pattern = self._generate_snake_pattern()
        if not pattern:
            QtWidgets.QMessageBox.warning(self, "Scan Error", "Invalid scan parameters.")
            return

        self._current_scan_bounds = {
            'xmin': self.scan_x_min.value(), 'xmax': self.scan_x_max.value(), 'dx': self.scan_x_step.value(),
            'ymin': self.scan_y_min.value(), 'ymax': self.scan_y_max.value(), 'dy': self.scan_y_step.value()
        }

        self._scan_pattern = pattern
        self._scan_index = 0
        self._scan_running = True

        self.btn_toggle_scan.setText("🛑 STOP HARDWARE SCAN")
        self.btn_toggle_scan.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; min-height: 35px;")
        
        logger.info(f"Starting event-driven scan: {len(self._scan_pattern)} points.")
        self._execute_current_scan_point()

    def _stop_scan(self):
        if not self._scan_running:
            return
        self._scan_running = False
        self.btn_toggle_scan.setText("🚀 START HARDWARE SCAN (Key: S)")
        self.btn_toggle_scan.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; min-height: 35px;")
        
        if self._current_scan_bounds:
            b = self._current_scan_bounds
            self.stage_map.add_scan_zone_to_history(b['xmin'], b['xmax'], b['dx'], b['ymin'], b['ymax'], b['dy'], completed=False)
        logger.info("Hardware scan stopped.")

    def _execute_current_scan_point(self):
        if not self._scan_running:
            return

        if self._scan_index >= len(self._scan_pattern):
            self._scan_running = False
            self.btn_toggle_scan.setText("🚀 START HARDWARE SCAN (Key: S)")
            self.btn_toggle_scan.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; min-height: 35px;")
            if self._current_scan_bounds:
                b = self._current_scan_bounds
                self.stage_map.add_scan_zone_to_history(b['xmin'], b['xmax'], b['dx'], b['ymin'], b['ymax'], b['dy'], completed=True)
            # QtWidgets.QMessageBox.information(self, "Scan Finished", "Snake scan successfully terminated !")
            return

        target_x, target_y = self._scan_pattern[self._scan_index]
        logger.info(f"[SCAN] Point {self._scan_index + 1}/{len(self._scan_pattern)} -> X={target_x:.3f}, Y={target_y:.3f}")

        EPSILON = 1e-4
        self._x_ready = (abs(self.widget_x.current_raw - target_x) < EPSILON)
        self._y_ready = (abs(self.widget_y.current_raw - target_y) < EPSILON)

        if self._x_ready and self._y_ready:
            QtCore.QTimer.singleShot(5, self._advance_scan_index)
            return

        # Only send move commands to axes that are not already at the target
        if not self._x_ready:
            self._move_x(target_x)
        if not self._y_ready:
            self._move_y(target_y)

    def _handle_axis_move_done_event(self, axis_tag: str):
        """Triggered exclusively by the hardware move_done_signal of the actuator controller."""
        if not self._scan_running:
            return

        if axis_tag == 'X':
            self._x_ready = True
        elif axis_tag == 'Y':
            self._y_ready = True

        # Advance to the next scan point only once both axes have acknowledged arrival
        if self._x_ready and self._y_ready:
            self._x_ready = False
            self._y_ready = False
            self._advance_scan_index()

    def _advance_scan_index(self):
        if not self._scan_running:
            return
        self._scan_index += 1
        self._execute_current_scan_point()

    def process_key_event(self, key, modifiers) -> bool:
        """Called by MainUIWidget.keyPressEvent when keyboard mode is armed.
        Returns True if the event was handled (caller will accept it),
        False to let Qt propagate it normally (e.g. Ctrl+Tab for tab switching).
        """
        # Never intercept Tab / Ctrl+Tab — let Qt handle tab switching.
        if key == QtCore.Qt.Key_Tab:
            return False

        if key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.widget_x.step_spinbox.setValue(self.widget_x.step_spinbox.value() * 10.0)
            self.widget_y.step_spinbox.setValue(self.widget_y.step_spinbox.value() * 10.0)
            return True
        if key == QtCore.Qt.Key_Minus:
            self.widget_x.step_spinbox.setValue(self.widget_x.step_spinbox.value() / 10.0)
            self.widget_y.step_spinbox.setValue(self.widget_y.step_spinbox.value() / 10.0)
            return True
        if key == QtCore.Qt.Key_S:
            self._on_toggle_scan_clicked()
            return True
        if key == QtCore.Qt.Key_Left:
            self._step_x(-self.widget_x.step_val); return True
        if key == QtCore.Qt.Key_Right:
            self._step_x(+self.widget_x.step_val); return True
        if key == QtCore.Qt.Key_Up:
            self._step_y(+self.widget_y.step_val); return True
        if key == QtCore.Qt.Key_Down:
            self._step_y(-self.widget_y.step_val); return True
        if key == QtCore.Qt.Key_Space:
            self._stop_all(); return True
        return False

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
        self.widget_x.setEnabled(self._actuator_x != "None")
        self.widget_y.setEnabled(self._actuator_y != "None")
        self.jog_pad.setEnabled(self._actuator_x != "None" and self._actuator_y != "None")
        
        self._connect_position_signals()
        self._refresh_map_saved()

    def _get_module(self, name: str):
        if not name or name == "None" or self.modules_manager is None:
            return None
        try:
            return self.modules_manager.get_mod_from_name(name, mod='act')
        except Exception as e:
            logger.warning(f"Actuator '{name}' not found: {e}")
            return None

    def _connect_position_signals(self):
        for sig, slot in self._signal_connections:
            try:
                sig.disconnect(slot)
            except Exception:
                pass
        self._signal_connections.clear()

        for axis_tag, mod_name, widget in (('X', self._actuator_x, self.widget_x), ('Y', self._actuator_y, self.widget_y)):
            mod = self._get_module(mod_name)
            if mod is None:
                continue

            # Safely read the native unit from the actuator module
            unit_str = "u.a."
            try:
                if hasattr(mod, 'units'):
                    unit_str = mod.units
                elif hasattr(mod, 'get_units'):
                    unit_str = mod.get_units()
            except Exception:
                pass
            widget.update_unit_display(unit_str)
            if axis_tag == 'X':
                self.stage_map.set_labels_units(unit_str)

            # Seed the displayed position from _current_value (set after INI_STAGE).
            # Must call .value() to extract the float — float(DataActuator) raises TypeError.
            try:
                data_act = getattr(mod, '_current_value', None) or getattr(mod, 'current_value', None)
                if data_act is not None and hasattr(data_act, 'value'):
                    widget.update_position(data_act.value())
                else:
                    mod.get_actuator_value()   # async fallback, result arrives via signal
            except Exception as exc:
                logger.warning(f"Initial position read failed for '{mod_name}': {exc}")
                try:
                    mod.get_actuator_value()
                except Exception:
                    pass

            # Connect exclusively to move_done_signal to avoid spurious refresh noise
            sig = getattr(mod, 'move_done_signal', None)
            if sig is not None:
                def make_slot(w, tag):
                    def slot(data_act):
                        try:
                            # Accept both DataActuator objects and raw floats
                            val = data_act.value() if hasattr(data_act, 'value') else float(data_act)
                        except Exception:
                            return
                        w.update_position(val)
                        w.set_busy(False)
                        self.stage_map.update_position(self.widget_x.current_raw, self.widget_y.current_raw)
                        self.saved_pos_widget.set_current_position(self.widget_x.current_raw, self.widget_y.current_raw)
                        
                        # Notify the scan engine that this axis has reached its target
                        self._handle_axis_move_done_event(tag)
                    return slot

                new_slot = make_slot(widget, axis_tag)
                try:
                    sig.connect(new_slot)
                    self._signal_connections.append((sig, new_slot))
                except Exception as e:
                    logger.warning(f"Could not connect to {mod_name}: {e}")

        self.stage_map.update_position(self.widget_x.current_raw, self.widget_y.current_raw)

    def _move_x(self, position: float):
        xmin, xmax = self.stage_map.x_limits
        if not (xmin <= position <= xmax):
            self.widget_x.set_status("⚠ Limits")
            return
        mod = self._get_module(self._actuator_x)
        if mod is None:
            self.widget_x.update_position(position)
            self.stage_map.update_position(position, self.widget_y.current_raw)
            self._handle_axis_move_done_event('X')
            return
        try:
            mod.move_abs(position)
            self.widget_x.set_busy(True)
        except Exception as e:
            logger.error(f"move_abs X failed: {e}")

    def _move_y(self, position: float):
        ymin, ymax = self.stage_map.y_limits
        if not (ymin <= position <= ymax):
            self.widget_y.set_status("⚠ Limits")
            return
        mod = self._get_module(self._actuator_y)
        if mod is None:
            self.widget_y.update_position(position)
            self.stage_map.update_position(self.widget_x.current_raw, position)
            self._handle_axis_move_done_event('Y')
            return
        try:
            mod.move_abs(position)
            self.widget_y.set_busy(True)
        except Exception as e:
            logger.error(f"move_abs Y failed: {e}")

    def _step_x(self, delta: float):
        new_pos = self.widget_x.current_raw + delta
        self.widget_x.target_spinbox.setValue(new_pos)
        self._move_x(new_pos)

    def _step_y(self, delta: float):
        new_pos = self.widget_y.current_raw + delta
        self.widget_y.target_spinbox.setValue(new_pos)
        self._move_y(new_pos)

    def _stop_x(self):
        mod = self._get_module(self._actuator_x)
        if mod:
            try: mod.stop()
            except Exception: pass
            self.widget_x.set_busy(False)

    def _stop_y(self):
        mod = self._get_module(self._actuator_y)
        if mod:
            try: mod.stop()
            except Exception: pass
            self.widget_y.set_busy(False)

    def _stop_all(self):
        if self._scan_running:
            self._stop_scan()
        self._stop_x()
        self._stop_y()

    def _go_to_saved(self, x: float, y: float):
        self._move_x(x)
        self._move_y(y)

    def _refresh_map_saved(self):
        self.stage_map.update_saved_positions(self.saved_pos_widget._positions)

    def get_config(self) -> dict:
        return {
            "pair_id": self.pair_id, "tab_title": self.tab_name_edit.text(),
            "actuator_x": self.combo_x.currentText(), "actuator_y": self.combo_y.currentText(),
            "step_x": self.widget_x.step_spinbox.value(), "step_y": self.widget_y.step_spinbox.value(),
            "limits": {
                "xmin": self.stage_map.x_min_sb.value(), "xmax": self.stage_map.x_max_sb.value(),
                "ymin": self.stage_map.y_min_sb.value(), "ymax": self.stage_map.y_max_sb.value(),
            },
            "saved_positions": self.saved_pos_widget._positions,
        }

    def load_config(self, config: dict):
        if not config: return
        self.combo_x.blockSignals(True)
        self.combo_y.blockSignals(True)
        try:
            if "tab_title"  in config: self.tab_name_edit.setText(config["tab_title"])
            if "actuator_x" in config: self.combo_x.setCurrentText(config["actuator_x"])
            if "actuator_y" in config: self.combo_y.setCurrentText(config["actuator_y"])
            self._actuator_x = self.combo_x.currentText()
            self._actuator_y = self.combo_y.currentText()
            if "step_x" in config: self.widget_x.step_spinbox.setValue(config["step_x"])
            if "step_y" in config: self.widget_y.step_spinbox.setValue(config["step_y"])
            if "limits" in config:
                lims = config["limits"]
                self.stage_map.x_min_sb.setValue(lims.get("xmin", -50.0))
                self.stage_map.x_max_sb.setValue(lims.get("xmax",  50.0))
                self.stage_map.y_min_sb.setValue(lims.get("ymin", -50.0))
                self.stage_map.y_max_sb.setValue(lims.get("ymax",  50.0))
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
        self._undo_positions = None
        self._setup_ui()
        self.scan_and_populate_profiles()

    def _setup_ui(self):
        self.main_layout = QtWidgets.QVBoxLayout(self)
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
        self.btn_save_all_pos = QtWidgets.QPushButton("📸 Save All Positions")
        self.btn_save_all_pos.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold;")
        self.toolbar.addWidget(self.btn_save_all_pos)

        self.btn_go_all_pos = QtWidgets.QPushButton("🚀 Go to All")
        self.btn_go_all_pos.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.toolbar.addWidget(self.btn_go_all_pos)

        self.btn_undo_all_pos = QtWidgets.QPushButton("↩️ Undo Move")
        self.btn_undo_all_pos.setStyleSheet("background-color: #e67e22; color: white; font-weight: bold;")
        self.btn_undo_all_pos.setEnabled(False)  # Désactivé tant qu'aucun mouvement global n'a eu lieu
        self.toolbar.addWidget(self.btn_undo_all_pos)

        self.toolbar.addStretch()

        self.btn_keyboard = QtWidgets.QPushButton("Keyboard Input: DISABLED")
        self.btn_keyboard.setCheckable(True)
        self.btn_keyboard.setStyleSheet("background-color: #e67e22; color: white; font-weight: bold; padding: 5px 10px;")
        self.toolbar.addWidget(self.btn_keyboard)
        self.main_layout.addLayout(self.toolbar)

        self.tabs_widget = QtWidgets.QTabWidget()
        self.tabs_widget.installEventFilter(self)
        self.tabs_widget.setTabsClosable(True)
        self.tabs_widget.tabCloseRequested.connect(self.close_tab)
        self.main_layout.addWidget(self.tabs_widget)

        self.btn_add_tab.clicked.connect(lambda: self.add_new_pair())
        self.btn_save_profile.clicked.connect(self.save_current_profile)
        self.btn_new_profile.clicked.connect(self.create_new_profile)
        self.btn_keyboard.toggled.connect(self.toggle_keyboard_mode)
        self.profile_combo.currentTextChanged.connect(self.load_profile)
        self.btn_save_all_pos.clicked.connect(self.save_all_tabs_positions)
        self.btn_go_all_pos.clicked.connect(self.go_to_all_positions)
        self.btn_undo_all_pos.clicked.connect(self.undo_last_global_move)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def toggle_keyboard_mode(self, checked):
        if checked:
            self.btn_keyboard.setText("Keyboard Input: ARMED")
            self.btn_keyboard.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; padding: 5px 10px;")
            self.setFocus()
        else:
            self.btn_keyboard.setText("Keyboard Input: DISABLED")
            self.btn_keyboard.setStyleSheet("background-color: #e67e22; color: white; font-weight: bold; padding: 5px 10px;")

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
        name, ok = QtWidgets.QInputDialog.getText(self, "Create Profile", "Enter unique profile name:")
        if ok and name.strip():
            target_path = self.CONFIG_DIR / f"{name.strip()}.json"
            if target_path.exists(): return
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump({"tabs": []}, f)
            self.profile_combo.addItem(name.strip())
            self.profile_combo.setCurrentText(name.strip())

    def load_profile(self, profile_name: str):
        if not profile_name: return
        self.tabs_widget.clear()
        self.tabs_list.clear()
        target_path = self.CONFIG_DIR / f"{profile_name}.json"
        if target_path.exists():
            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for tab_config in config.get("tabs", []):
                    self.add_new_pair(tab_config)
            except Exception:
                pass
        if not self.tabs_list:
            self.add_new_pair()

    def save_current_profile(self):
        profile_name = self.profile_combo.currentText()
        if not profile_name: return
        config = {"tabs": [tab.get_config() for tab in self.tabs_list]}
        target_path = self.CONFIG_DIR / f"{profile_name}.json"
        try:
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            QtWidgets.QMessageBox.information(self, "Saved", f"Profile '{profile_name}' saved.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Write error: {e}")

    def save_all_tabs_positions(self):
        """Captures current positions of X and Y for all active group tabs 
        and saves them under a common timestamped or custom name.
        """
        if not self.tabs_list:
            return

        # Demander un nom de base à l'utilisateur
        default_name = f"Snapshot_{QtCore.QDateTime.currentDateTime().toString('yyyyMMdd_hhmmss')}"
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save All Positions", 
            "Enter a base name for this global position snapshot:",
            text=default_name
        )
        
        if not ok or not name.strip():
            return
            
        base_name = name.strip()

        # Itérer sur tous les onglets pour enregistrer leurs positions courantes
        for idx, tab in enumerate(self.tabs_list):
            # Générer un nom unique par onglet s'ils n'ont pas de titre explicite
            tab_title = tab.tab_name_edit.text().strip() or f"Group_{idx+1}"
            unique_pos_name = f"{base_name} ({tab_title})"
            
            # Récupérer les positions X et Y actuelles stockées dans l'onglet
            current_x = tab.widget_x.current_raw
            current_y = tab.widget_y.current_raw
            
            # Injecter directement dans le dictionnaire interne de l'onglet
            tab.saved_pos_widget._positions[unique_pos_name] = {"x": current_x, "y": current_y}
            
            # Rafraîchir l'affichage de la table et de la carte pour cet onglet
            tab.saved_pos_widget.refresh_table()
            tab.saved_pos_widget.positions_changed.emit()
            
        # Forcer une sauvegarde silencieuse dans le fichier JSON du profil
        self.silent_save_profile()
        
        QtWidgets.QMessageBox.information(
            self, "Success", 
            f"Positions saved across all {len(self.tabs_list)} tabs under the prefix '{base_name}'."
        )

    def go_to_all_positions(self):
        """Finds all global positions saved under a common prefix, 
        saves current state for Undo, and moves all matching motors.
        """
        if not self.tabs_list:
            return

        # 1. Collecter tous les noms de positions disponibles dans TOUS les onglets
        all_prefixes = set()
        for tab in self.tabs_list:
            for full_name in tab.saved_pos_widget._positions.keys():
                if " (" in full_name:
                    prefix = full_name.split(" (")[0]
                    all_prefixes.add(prefix)
                else:
                    all_prefixes.add(full_name)

        if not all_prefixes:
            QtWidgets.QMessageBox.warning(self, "Go to All", "No saved positions found in any tab.")
            return

        # 2. Demander à l'utilisateur de choisir le snapshot global à charger
        prefix_list = sorted(list(all_prefixes))
        item, ok = QtWidgets.QInputDialog.getItem(
            self, "Go to All Positions", 
            "Select the global snapshot position to restore:", 
            prefix_list, editable=False
        )
        
        if not ok or not item:
            return

        # 3. SAUVEGARDE SÉCURITÉ (UNDO) : Enregistrer l'état actuel avant le mouvement
        self._undo_positions = {}
        for idx, tab in enumerate(self.tabs_list):
            self._undo_positions[tab] = {
                "x": tab.widget_x.current_raw,
                "y": tab.widget_y.current_raw
            }

        # 4. EXÉCUTION DU MOUVEMENT GLOBAL DIRECT SUR LE MODULE PYMODAQ
        move_count = 0
        for idx, tab in enumerate(self.tabs_list):
            tab_title = tab.tab_name_edit.text().strip() or f"Group_{idx+1}"
            expected_name = f"{item} ({tab_title})"
            
            pos_data = tab.saved_pos_widget._positions.get(expected_name) or tab.saved_pos_widget._positions.get(item)
            
            if pos_data:
                target_x = pos_data.get("x", 0.0)
                target_y = pos_data.get("y", 0.0)
                
                # Récupérer directement les modules matériels PyMoDAQ (via le helper qui
                # passe bien mod='act' et gère les erreurs / "None")
                mod_x = tab._get_module(tab._actuator_x)
                mod_y = tab._get_module(tab._actuator_y)
                
                # C'est ici qu'on appelle DIRECTEMENT l'ordre PyMoDAQ sans passer par le signal du widget
                if mod_x is not None:
                    try:
                        mod_x.move_abs(target_x)
                        move_count += 1
                    except Exception as e:
                        logger.error(f"move_abs X failed for tab '{tab_title}': {e}")
                if mod_y is not None:
                    try:
                        mod_y.move_abs(target_y)
                        move_count += 1
                    except Exception as e:
                        logger.error(f"move_abs Y failed for tab '{tab_title}': {e}")

        if move_count > 0:
            self.btn_undo_all_pos.setEnabled(True)

    def undo_last_global_move(self):
        """Restores the exact positions the motors were at before the last 'Go to All' command."""
        if not self._undo_positions:
            return
            
        move_count = 0
        for tab, previous_coords in self._undo_positions.items():
            if tab in self.tabs_list:
                mod_x = tab._get_module(tab._actuator_x)
                mod_y = tab._get_module(tab._actuator_y)
                
                # Rappeler l'ancienne position directement sur l'actionneur PyMoDAQ
                if mod_x is not None:
                    try:
                        mod_x.move_abs(previous_coords["x"])
                        move_count += 1
                    except Exception as e:
                        logger.error(f"Undo move_abs X failed: {e}")
                if mod_y is not None:
                    try:
                        mod_y.move_abs(previous_coords["y"])
                        move_count += 1
                    except Exception as e:
                        logger.error(f"Undo move_abs Y failed: {e}")

        self._undo_positions = None
        self.btn_undo_all_pos.setEnabled(False)
        QtWidgets.QMessageBox.information(self, "Undo Success", f"Restored previous positions on {move_count} axes.")

        
    def add_new_pair(self, config_data=None):
        pair_id = config_data.get("pair_id", f"tab_{QtCore.QDateTime.currentMSecsSinceEpoch()}") if config_data else f"tab_{QtCore.QDateTime.currentMSecsSinceEpoch()}"
        new_pair = StagePairWidget(self.modules_manager, pair_id)
        if config_data:
            new_pair.load_config(config_data)
        self.tabs_list.append(new_pair)
        self.tabs_widget.addTab(new_pair, new_pair.tab_name_edit.text())
        new_pair.tab_name_edit.textChanged.connect(lambda text, w=new_pair: self.tabs_widget.setTabText(self.tabs_widget.indexOf(w), text))
        new_pair.saved_pos_widget.positions_changed.connect(self.silent_save_profile)

    def close_tab(self, index):
        if self.tabs_widget.count() <= 1: 
            return
        widget = self.tabs_widget.widget(index)
        if widget in self.tabs_list: 
            # Déconnexion explicite des signaux hardware avant destruction
            if hasattr(widget, '_signal_connections'):
                for sig, slot in widget._signal_connections:
                    try: sig.disconnect(slot)
                    except Exception: pass
            self.tabs_list.remove(widget)
            
        self.tabs_widget.removeTab(index)
        widget.deleteLater()

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        # T / Shift+T: cycle through group tabs forward or backward (no keyboard-armed mode required).
        if event.key() == QtCore.Qt.Key_T and not event.isAutoRepeat():
            total_tabs = self.tabs_widget.count()
            if total_tabs > 1:
                current_idx = self.tabs_widget.currentIndex()
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    next_idx = (current_idx - 1) % total_tabs
                else:
                    next_idx = (current_idx + 1) % total_tabs
                self.tabs_widget.setCurrentIndex(next_idx)
            event.accept()
            return
        if not self.btn_keyboard.isChecked() or event.isAutoRepeat():
            super().keyPressEvent(event)
            return
        current_idx = self.tabs_widget.currentIndex()
        if current_idx == -1:
            super().keyPressEvent(event)
            return
        active_tab = self.tabs_widget.widget(current_idx)
        if active_tab and active_tab.process_key_event(event.key(), event.modifiers()):
            event.accept()
        else:
            super().keyPressEvent(event)
            
    def eventFilter(self, watched, event):
        # Intercept T / Shift+T inside the tab widget before child widgets (e.g. spinboxes) consume it.
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() == QtCore.Qt.Key_T and not event.isAutoRepeat():
                current_idx = self.tabs_widget.currentIndex()
                total_tabs = self.tabs_widget.count()
                if total_tabs > 1:
                    if event.modifiers() & QtCore.Qt.ShiftModifier:
                        next_idx = (current_idx - 1) % total_tabs
                    else:
                        next_idx = (current_idx + 1) % total_tabs
                    self.tabs_widget.setCurrentIndex(next_idx)
                return True  # Event consumed
        
        return super().eventFilter(watched, event)
    
    def silent_save_profile(self):
        profile_name = self.profile_combo.currentText()
        if not profile_name: return
        config = {"tabs": [tab.get_config() for tab in self.tabs_list]}
        try:
            with open(self.CONFIG_DIR / f"{profile_name}.json", 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
        except Exception: pass


# ===========================================================================
# Main extension class (PyMoDAQ interface)
# ===========================================================================
class XYStageController(CustomExt):
    params = []

    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)
        path = Path(str(self.dashboard.preset_file))
        self.preset_name = path.stem
        self.setup_ui()

    def setup_actions(self): pass
    def connect_things(self): pass

    def setup_docks(self):
        self.docks['main'] = gutils.Dock(f'Stage Controller - {self.preset_name}')
        self.dockarea.addDock(self.docks['main'])
        self.main_ui = MainUIWidget(self.dashboard.modules_manager, self.preset_name)
        self.docks['main'].addWidget(self.main_ui)