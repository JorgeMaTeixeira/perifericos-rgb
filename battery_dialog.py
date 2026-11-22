"""Janela de visualização do histórico de bateria (gráfico + tabela)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from battery_log import clear_device_history, format_battery_summary, get_device_history


class _BatteryChartWidget(QWidget):
    """Gráfico simples de evolução da bateria (0-100%)."""

    def __init__(self, samples: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples = samples
        self.setMinimumHeight(140)

    def set_samples(self, samples: list[dict]) -> None:
        self._samples = samples
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        left, right, top, bottom = 38, 12, 18, 22
        plot_w = max(1, w - left - right)
        plot_h = max(1, h - top - bottom)

        # Fundo do gráfico
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#14171d")))
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        # Linhas de referência (0%, 50%, 100%)
        grid_pen = QPen(QColor("#262b36"), 1, Qt.PenStyle.DashLine)
        text_pen = QPen(QColor("#7c8598"))
        for pct in (0, 50, 100):
            y = top + plot_h * (1.0 - pct / 100.0)
            painter.setPen(grid_pen)
            painter.drawLine(left, int(y), w - right, int(y))
            painter.setPen(text_pen)
            painter.drawText(4, int(y) + 4, f"{pct}%")

        if not self._samples:
            painter.setPen(QColor("#7c8598"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sem registos ainda")
            painter.end()
            return

        # Pontos normalizados no tempo
        ts_vals = [float(s.get("ts", 0)) for s in self._samples]
        t_min, t_max = min(ts_vals), max(ts_vals)
        t_span = max(1.0, t_max - t_min)

        pts: list[tuple[QPointF, int, bool]] = []
        for idx, s in enumerate(self._samples):
            pct = max(0, min(100, int(s.get("pct", 0))))
            ts = float(s.get("ts", 0))
            if len(self._samples) == 1:
                x = left + plot_w * 0.5
            else:
                x = left + plot_w * ((ts - t_min) / t_span)
            y = top + plot_h * (1.0 - pct / 100.0)
            pts.append((QPointF(x, y), pct, bool(s.get("charging"))))

        # Área sombreada
        if len(pts) >= 2:
            area = QPainterPath()
            area.moveTo(pts[0][0].x(), top + plot_h)
            for pt, _, _ in pts:
                area.lineTo(pt)
            area.lineTo(pts[-1][0].x(), top + plot_h)
            area.closeSubpath()

            grad = QLinearGradient(0, top, 0, top + plot_h)
            grad.setColorAt(0.0, QColor(79, 140, 255, 80))
            grad.setColorAt(1.0, QColor(79, 140, 255, 5))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPath(area)

            line_path = QPainterPath()
            line_path.moveTo(pts[0][0])
            for pt, _, _ in pts[1:]:
                line_path.lineTo(pt)
            painter.setPen(QPen(QColor("#4f8cff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(line_path)

        # Pontos individuais
        for pt, pct, charging in pts:
            if charging:
                dot_color = QColor("#ffd60a")
            elif pct > 50:
                dot_color = QColor("#4cd964")
            elif pct > 20:
                dot_color = QColor("#ffd60a")
            else:
                dot_color = QColor("#ff453a")
            painter.setPen(QPen(QColor("#14171d"), 1.5))
            painter.setBrush(QBrush(dot_color))
            painter.drawEllipse(pt, 3.5, 3.5)

        # Etiquetas de data/hora (início e fim)
        painter.setPen(text_pen)
        fmt = "%d/%m %H:%M"
        t_start_lbl = datetime.fromtimestamp(t_min).strftime(fmt)
        t_end_lbl = datetime.fromtimestamp(t_max).strftime(fmt)
        painter.drawText(left, h - 5, t_start_lbl)
        if len(pts) > 1:
            end_w = painter.fontMetrics().horizontalAdvance(t_end_lbl)
            painter.drawText(w - right - end_w, h - 5, t_end_lbl)

        painter.end()


class BatteryHistoryDialog(QDialog):
    """Diálogo com gráfico e tabela do histórico de bateria de um periférico."""

    def __init__(
        self,
        device_key: str,
        device_label: str,
        current_pct: int | None = None,
        is_charging: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._device_key = device_key
        self._current_pct = current_pct
        self._is_charging = is_charging

        self.setWindowTitle(f"Histórico de Bateria — {device_label}")
        self.setMinimumSize(500, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(f"Histórico de Bateria · {device_label}")
        title.setObjectName("title")
        layout.addWidget(title)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setObjectName("subtitle")
        layout.addWidget(self._summary_lbl)

        chart_card = QFrame()
        chart_card.setObjectName("card")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(10, 10, 10, 10)
        self._chart = _BatteryChartWidget([])
        chart_layout.addWidget(self._chart)
        layout.addWidget(chart_card)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Data / Hora", "Bateria", "Estado"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.setStyleSheet(
            "QTableWidget { background-color: #1b1e25; border: 1px solid #2b303b; "
            "border-radius: 8px; gridline-color: #262b36; color: #e6e9ef; }"
            "QHeaderView::section { background-color: #252933; color: #9aa3b2; "
            "border: none; padding: 6px; font-weight: 600; }"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Atualizar")
        refresh_btn.clicked.connect(self._reload)
        clear_btn = QPushButton("Limpar Histórico")
        clear_btn.clicked.connect(self._clear)
        close_btn = QPushButton("Fechar")
        close_btn.setObjectName("primaryBtn")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._reload()

    def _reload(self) -> None:
        entry = get_device_history(self._device_key)
        samples = entry.get("samples", [])
        if not isinstance(samples, list):
            samples = []

        summary = format_battery_summary(
            self._device_key, self._current_pct, self._is_charging
        )
        count_txt = f"{len(samples)} registos"
        self._summary_lbl.setText(f"{summary} · {count_txt}" if summary else count_txt)

        self._chart.set_samples(samples)

        self._table.setRowCount(len(samples))
        for row_idx, sample in enumerate(reversed(samples)):
            ts = float(sample.get("ts", 0))
            pct = int(sample.get("pct", 0))
            charging = bool(sample.get("charging", False))

            dt_str = datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M") if ts > 0 else "--"
            dt_item = QTableWidgetItem(dt_str)
            pct_item = QTableWidgetItem(f"{pct}%")
            pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if pct > 50:
                pct_item.setForeground(QColor("#4cd964"))
            elif pct > 20:
                pct_item.setForeground(QColor("#ffd60a"))
            else:
                pct_item.setForeground(QColor("#ff453a"))

            state_item = QTableWidgetItem("⚡ A carregar" if charging else "Sem fios (2.4G)")
            state_item.setForeground(QColor("#ffd60a" if charging else "#9aa3b2"))

            self._table.setItem(row_idx, 0, dt_item)
            self._table.setItem(row_idx, 1, pct_item)
            self._table.setItem(row_idx, 2, state_item)

    def _clear(self) -> None:
        clear_device_history(self._device_key)
        self._reload()
