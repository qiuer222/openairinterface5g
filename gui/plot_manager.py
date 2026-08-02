#!/usr/bin/env python3
"""pyqtgraph panels for throughput, PDSCH metrics and CSI channel quality."""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List

import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


pg.setConfigOptions(antialias=True)

MAX_SAMPLES = 50


class MetricPlot(QWidget):
    """A title bar with metric selector plus one rolling pyqtgraph curve."""

    def __init__(self, title: str, metrics: List[str], parent=None):
        super().__init__(parent)
        self.metrics = metrics
        self.active = metrics[0]
        self.time: Deque[float] = deque(maxlen=MAX_SAMPLES)
        self.series: Dict[str, Deque[float]] = {
            name: deque(maxlen=MAX_SAMPLES) for name in metrics
        }

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(title))
        self.combo = QComboBox()
        self.combo.addItems(metrics)
        self.combo.currentTextChanged.connect(self._set_active)
        top.addWidget(self.combo)
        top.addStretch()
        layout.addLayout(top)

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.enableAutoRange(axis="x", enable=True)
        self.plot.enableAutoRange(axis="y", enable=True)
        self.curve = self.plot.plot(pen=pg.mkPen("#0066cc", width=2))
        layout.addWidget(self.plot, 1)

    def _set_active(self, name: str) -> None:
        self.active = name
        if self.time:
            self.curve.setData(list(self.time), list(self.series[name]))

    def add_sample(self, t: float, values: Dict[str, float]) -> None:
        self.time.append(t)
        for name in self.metrics:
            self.series[name].append(float(values.get(name, 0.0)))
        self.curve.setData(list(self.time), list(self.series[self.active]))
        self.plot.getViewBox().updateAutoRange()


class PlotManager(QWidget):
    """Three vertically-stacked metric plots."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.throughput = MetricPlot("Throughput", ["throughput"])
        self.pdsch = MetricPlot(
            "PDSCH Metrics", ["bler", "rsrp", "sinr", "mcs", "nprb"]
        )
        self.csi = MetricPlot(
            "CSI Channel Quality",
            ["capacity", "rank", "condition_number"],
        )
        for panel in (self.throughput, self.pdsch, self.csi):
            layout.addWidget(panel, 1)
