import copy

from PySide2.QtCore import Qt, QObject, Signal

from itertools import cycle

import numpy as np

import matplotlib.pyplot as plt
from matplotlib.widgets import Cursor

from hexrd.ui import enter_key_filter

from hexrd.ui.constants import ViewType
from hexrd.ui.ui_loader import UiLoader
from hexrd.ui.zoom_canvas import ZoomCanvas


class LinePickerDialog(QObject):

    # Emitted when a point was picked
    point_picked = Signal()

    # Emitted when a line is completed
    line_completed = Signal()

    # Emitted when the dialog is closed
    finished = Signal()

    # Emits the ring data that was selected
    result = Signal(list)

    # Emitted when the last point was removed
    last_point_removed = Signal()

    def __init__(self, canvas, parent, single_line_mode=False):
        super(LinePickerDialog, self).__init__(parent)

        self.canvas = canvas

        loader = UiLoader()
        self.ui = loader.load_file('line_picker_dialog.ui', parent)

        self.single_line_mode = single_line_mode
        self.ui.start_new_line_label.setVisible(not self.single_line_mode)

        flags = self.ui.windowFlags()
        self.ui.setWindowFlags(flags | Qt.Tool)
        self.ui.installEventFilter(enter_key_filter)

        self.ring_data = []
        self.linebuilder = None
        self.lines = []

        self.two_click_mode = False

        self.zoom_canvas = ZoomCanvas(canvas)
        self.zoom_canvas.tth_tol = self.ui.zoom_tth_width.value()
        self.zoom_canvas.eta_tol = self.ui.zoom_eta_width.value()
        self.ui.zoom_canvas_layout.addWidget(self.zoom_canvas)

        prop_cycle = plt.rcParams['axes.prop_cycle']
        self.color_cycler = cycle(prop_cycle.by_key()['color'])

        self.move_dialog_to_left()

        self.setup_connections()

    def setup_connections(self):
        self.ui.accepted.connect(self.accept)
        self.ui.rejected.connect(self.reject)
        self.ui.zoom_tth_width.valueChanged.connect(self.zoom_width_changed)
        self.ui.zoom_eta_width.valueChanged.connect(self.zoom_width_changed)
        self.ui.back_button.pressed.connect(self.back_button_pressed)
        self.point_picked.connect(self.update_enable_states)
        self.last_point_removed.connect(self.update_enable_states)
        self.ui.two_click_mode.toggled.connect(self.two_click_mode_changed)
        self.bp_id = self.canvas.mpl_connect('button_press_event',
                                             self.button_pressed)
        self.zoom_canvas.point_picked.connect(self.zoom_point_picked)

    def update_enable_states(self):
        linebuilder = self.linebuilder
        enable_back_button = (
            linebuilder is not None and
            all(z for z in [linebuilder.xs, linebuilder.ys])
        )
        self.ui.back_button.setEnabled(enable_back_button)

    def move_dialog_to_left(self):
        # This moves the dialog to the left border of the parent
        ph = self.ui.parent().geometry().height()
        px = self.ui.parent().geometry().x()
        py = self.ui.parent().geometry().y()
        dw = self.ui.width()
        dh = self.ui.height()
        self.ui.setGeometry(px, py + (ph - dh) / 2.0, dw, dh)

    def clear(self):
        self.ring_data.clear()

        while self.lines:
            self.lines.pop(0).remove()

        self.linebuilder = None
        self.cursor = None

        self.zoom_canvas.cleanup()
        self.zoom_canvas = None

        self.canvas.mpl_disconnect(self.bp_id)
        self.bp_id = None
        self.canvas.draw()

    def zoom_width_changed(self):
        self.zoom_canvas.tth_tol = self.ui.zoom_tth_width.value()
        self.zoom_canvas.eta_tol = self.ui.zoom_eta_width.value()
        self.zoom_canvas.render()

    def zoom_point_picked(self, event):
        self.zoom_frozen = False
        if self.linebuilder is None:
            return

        # Append the data to the line builder
        self.linebuilder.append_data(event.xdata, event.ydata)

    def back_button_pressed(self):
        linebuilder = self.linebuilder
        if linebuilder is None:
            # Nothing to do
            return

        if not linebuilder.xs or not linebuilder.ys:
            # Nothing to delete
            return

        linebuilder.xs.pop(-1)
        linebuilder.ys.pop(-1)
        linebuilder.update_line_data()

        self.last_point_removed.emit()

    def two_click_mode_changed(self, on):
        self.two_click_mode = on
        self.zoom_frozen = False

    def start(self):
        if self.canvas.mode != ViewType.polar:
            print('line picker only works in polar mode!')
            return

        ax = self.canvas.axis

        # list for set of rings 'picked'
        self.ring_data.clear()

        # fire up the cursor for this tool
        self.cursor = Cursor(ax, useblit=True, color='red', linewidth=1)
        self.add_line()
        self.show()

    def add_line(self):
        ax = self.canvas.axis
        color = next(self.color_cycler)
        marker = '.'
        linestyle = 'None'

        # empty line
        line, = ax.plot([], [], color=color, marker=marker,
                        linestyle=linestyle)
        self.linebuilder = LineBuilder(line)

        self.linebuilder.point_picked.connect(self.point_picked.emit)

        self.update_enable_states()

        self.lines.append(line)
        self.canvas.draw()

    def line_finished(self):
        linebuilder = self.linebuilder
        # If the linebuilder is already gone, just return
        if linebuilder is None:
            return

        # append to ring_data
        ring_data = np.vstack([linebuilder.xs, linebuilder.ys]).T

        if len(ring_data) == 0:
            # Don't do anything if there is no ring data
            return

        self.ring_data.append(ring_data)
        self.add_line()

    def button_pressed(self, event):
        if event.button == 3:
            # Advance the line to the next one
            self.next_line()
            return

        if event.button != 1:
            # Nothing else to do
            return

        if self.two_click_mode:
            # Freeze the zoom window
            self.zoom_frozen = True
            return

        if self.linebuilder is None:
            return

        self.linebuilder.append_data(event.xdata, event.ydata)

    def next_line(self):
        if not self.single_line_mode:
            # Complete a line
            self.line_completed.emit()
            self.line_finished()
            return

        # Otherwise, insert NaNs
        if self.linebuilder is None:
            return

        self.linebuilder.append_data(np.nan, np.nan)

    def accept(self):
        # Finish the current line
        self.line_finished()

        # finished needs to be emitted before the result
        self.finished.emit()
        self.result.emit(copy.deepcopy(self.ring_data))

        self.clear()

    def reject(self):
        self.finished.emit()
        self.clear()

    def show(self):
        self.ui.show()

    @property
    def zoom_frozen(self):
        return self.zoom_canvas.frozen

    @zoom_frozen.setter
    def zoom_frozen(self, v):
        self.zoom_canvas.frozen = v


class LineBuilder(QObject):

    # Emits when a point was picked
    point_picked = Signal()

    def __init__(self, line):
        super().__init__()

        self.line = line
        self.canvas = line.figure.canvas
        self.xs = list(line.get_xdata())
        self.ys = list(line.get_ydata())

    def append_data(self, x, y):
        self.xs.append(x)
        self.ys.append(y)
        self.update_line_data()
        self.point_picked.emit()

    def update_line_data(self):
        self.line.set_data(self.xs, self.ys)
        self.canvas.draw()
