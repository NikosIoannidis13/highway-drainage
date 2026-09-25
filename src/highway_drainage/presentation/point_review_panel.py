"""Controls for explicit inlet/outlet classification on the drawing preview."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from highway_drainage.domain.crossings import CrossingResult, PointRole
from highway_drainage.presentation.crossing_view import CrossingView


class PointReviewPanel(QWidget):
    role_requested = Signal(object)
    undo_requested = Signal()
    inlet_requested = Signal(float, float, str)

    def __init__(self, view: CrossingView) -> None:
        super().__init__()
        self.view = view
        self.result: CrossingResult | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.tool = QComboBox()
        self.tool.addItem("Pan", "pan")
        self.tool.addItem("Select points", "select")
        self.tool.addItem("Add inlet point", "add_inlet")
        self.tool.setAccessibleName("Point selection tool")
        self.tool.currentIndexChanged.connect(self._tool_changed)
        tools.addWidget(self.tool)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_requested)
        tools.addWidget(self.undo_button)
        tools.addStretch()
        layout.addLayout(tools)
        roles = QHBoxLayout()
        self.inlet_button = QPushButton("Mark as inlet")
        self.outlet_button = QPushButton("Mark as outlet")
        self.clear_button = QPushButton("Clear classification")
        for button, role in (
            (self.inlet_button, PointRole.INLET),
            (self.outlet_button, PointRole.OUTLET),
            (self.clear_button, PointRole.UNCLASSIFIED),
        ):
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, role=role: self.role_requested.emit(role))
            roles.addWidget(button)
        layout.addLayout(roles)
        self.manual_row = QWidget()
        manual = QHBoxLayout(self.manual_row)
        manual.setContentsMargins(0, 0, 0, 0)
        manual.addWidget(QLabel("Culvert for added inlet"))
        self.culvert = QComboBox()
        self.culvert.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.culvert.setMinimumContentsLength(12)
        manual.addWidget(self.culvert, 1)
        layout.addWidget(self.manual_row)
        self.manual_row.hide()
        self.counts = QLabel("Find crossings to classify culvert points.")
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)
        self.hint = QLabel(
            "Select points: click or drag a rectangle; Shift adds points. "
            "Only inlets are used for catchments. Point editing switches satellite "
            "imagery to the drawing preview."
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        view.selection_changed.connect(self._selection_changed)
        view.manual_point_requested.connect(self._add_inlet)
        self.tool.setEnabled(False)

    def _tool_changed(self) -> None:
        mode = str(self.tool.currentData())
        self.view.set_interaction_mode(mode)
        self.manual_row.setVisible(mode == "add_inlet")

    def _add_inlet(self, x: float, y: float) -> None:
        self.inlet_requested.emit(x, y, str(self.culvert.currentData() or ""))

    def show_result(self, result: CrossingResult | None, *, can_undo: bool = False) -> None:
        self.result = result
        previous = self.culvert.currentData()
        self.culvert.clear()
        self.culvert.addItem("Choose a culvert…", "")
        if result is not None:
            for line in result.culverts:
                self.culvert.addItem(line.reference.label, line.reference.identifier)
            index = self.culvert.findData(previous)
            if index >= 0:
                self.culvert.setCurrentIndex(index)
            elif len(result.culverts) == 1:
                self.culvert.setCurrentIndex(1)
        self.tool.setEnabled(result is not None)
        self.undo_button.setEnabled(can_undo)
        if result is None:
            self.tool.setCurrentIndex(0)
        self._selection_changed(self.view.selected_ids)

    def _selection_changed(self, identifiers: set[str]) -> None:
        for button in (self.inlet_button, self.outlet_button, self.clear_button):
            button.setEnabled(bool(identifiers) and self.result is not None)
        if self.result is None:
            self.counts.setText("Find crossings to classify culvert points.")
            return
        counts = {role: sum(p.role == role for p in self.result.points) for role in PointRole}
        self.counts.setText(
            f"{counts[PointRole.INLET]} inlets · {counts[PointRole.OUTLET]} outlets · "
            f"{counts[PointRole.UNCLASSIFIED]} unassigned · {len(identifiers)} selected"
        )
        culverts = {p.culvert.identifier for p in self.result.points if p.identifier in identifiers}
        if len(culverts) == 1:
            index = self.culvert.findData(next(iter(culverts)))
            if index >= 0:
                self.culvert.setCurrentIndex(index)
