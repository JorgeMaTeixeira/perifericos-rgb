"""Separador de controlo do rato Glorious Model I 2 Wireless."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from automation import load_config as load_auto_config
from automation import notify_full_battery, notify_low_battery
from battery_dialog import BatteryHistoryDialog
from battery_log import format_battery_summary, record_battery
from glorious_hid import (
    DPI_MAX,
    DPI_MIN,
    DPI_STEP,
    LIFT_OFF_OPTIONS,
    MOUSE_COLOR_EFFECTS,
    MOUSE_EFFECTS,
    MOUSE_STATE_PATH,
    POLLING_RATES,
    GloriousMouseDevice,
    MouseState,
    load_mouse_state,
    save_mouse_state,
)

MOUSE_COLOR_PRESETS: list[tuple[str, tuple[int, int, int], str]] = [
    ("Branco", (255, 255, 255), "#ffffff"),
    ("Vermelho", (255, 40, 40), "#ff3b30"),
    ("Laranja", (255, 110, 0), "#ff7b00"),
    ("Amarelo", (255, 214, 10), "#ffd60a"),
    ("Verde", (48, 209, 88), "#30d158"),
    ("Ciano", (50, 212, 222), "#32d4de"),
    ("Azul", (10, 132, 255), "#0a84ff"),
    ("Roxo", (191, 90, 242), "#bf5af2"),
    ("Rosa", (255, 55, 95), "#ff375f"),
]


class MouseTab(QWidget):
    """Painel de bateria e iluminação RGB do Glorious Model I 2 Wireless."""

    syncColorRequested = Signal(tuple)
    batteryUpdated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device = GloriousMouseDevice()
        self.state: MouseState = load_mouse_state()
        self._syncing = False
        self._low_bat_alerted = False
        self._full_bat_alerted = False
        self._saver_active = False
        self._pre_saver_brightness: int | None = None
        self._last_active_effect = (
            self.state.effect if self.state.effect > 0 else self.state.last_active_effect
        )
        self._state_mtime: float = self._get_state_mtime()

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(80)
        self._apply_timer.timeout.connect(self._send_lighting)

        self._battery_timer = QTimer(self)
        self._battery_timer.setInterval(1500)
        self._battery_timer.timeout.connect(self._poll_battery)

        self._build_ui()
        self._load_ui_from_state()
        self.connect_mouse()
        self._battery_timer.start()

    @staticmethod
    def _make_spin(min_v: int, max_v: int, suffix: str = "%") -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName("valueBadge")
        spin.setRange(min_v, max_v)
        spin.setSuffix(suffix)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setKeyboardTracking(False)
        return spin

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        # Cabeçalho
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("Glorious Model I 2 Wireless")
        title_lbl.setObjectName("title")
        self.status_lbl = QLabel("A procurar rato...")
        self.status_lbl.setObjectName("subtitle")
        title_box.addWidget(title_lbl)
        title_box.addWidget(self.status_lbl)

        self.power_btn = QPushButton("Luzes: ON")
        self.power_btn.setObjectName("powerBtn")
        self.power_btn.setCheckable(True)
        self.power_btn.clicked.connect(self._on_power_toggled)

        self.reconnect_btn = QPushButton("Reconectar")
        self.reconnect_btn.clicked.connect(self.connect_mouse)

        header.addLayout(title_box, 1)
        header.addWidget(self.power_btn)
        header.addWidget(self.reconnect_btn)
        layout.addLayout(header)

        # Card: Bateria
        bat_card = QFrame()
        bat_card.setObjectName("card")
        bat_layout = QVBoxLayout(bat_card)
        bat_layout.setContentsMargins(16, 12, 16, 14)
        bat_layout.setSpacing(8)

        bat_top = QHBoxLayout()
        bat_header = QLabel("Bateria")
        bat_header.setObjectName("sectionTitle")
        self.charging_lbl = QLabel("")
        self.charging_lbl.setObjectName("subtitle")
        self.history_btn = QPushButton("Histórico")
        self.history_btn.setStyleSheet("padding: 3px 9px; font-size: 11px;")
        self.history_btn.setToolTip("Ver gráfico e registo de bateria do rato")
        self.history_btn.clicked.connect(self._open_battery_history)
        bat_top.addWidget(bat_header)
        bat_top.addStretch()
        bat_top.addWidget(self.charging_lbl)
        bat_top.addWidget(self.history_btn)
        bat_layout.addLayout(bat_top)

        bat_row = QHBoxLayout()
        bat_row.setSpacing(10)
        self.battery_bar = QProgressBar()
        self.battery_bar.setRange(0, 100)
        self.battery_bar.setTextVisible(False)
        self.battery_bar.setFixedHeight(10)
        self.battery_pct_lbl = QLabel("--%")
        self.battery_pct_lbl.setObjectName("subtitle")
        self.battery_pct_lbl.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #f0f3f8; min-width: 46px;"
        )
        self.battery_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bat_row.addWidget(self.battery_bar, 1)
        bat_row.addWidget(self.battery_pct_lbl)
        bat_layout.addLayout(bat_row)

        self.battery_note_lbl = QLabel(
            "Atualiza automaticamente em 2.4G quando mexes no rato."
        )
        self.battery_note_lbl.setObjectName("subtitle")
        bat_layout.addWidget(self.battery_note_lbl)
        layout.addWidget(bat_card)

        # Card: Iluminação
        self.fx_card = QFrame()
        self.fx_card.setObjectName("card")
        fx_layout = QVBoxLayout(self.fx_card)
        fx_layout.setContentsMargins(16, 14, 16, 16)
        fx_layout.setSpacing(12)

        fx_header = QLabel("Iluminação")
        fx_header.setObjectName("sectionTitle")
        fx_layout.addWidget(fx_header)

        eff_row = QHBoxLayout()
        eff_lbl = QLabel("Efeito")
        eff_lbl.setFixedWidth(85)
        self.effect_combo = QComboBox()
        for eff_id, eff_name in MOUSE_EFFECTS:
            self.effect_combo.addItem(eff_name, eff_id)
        self.effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        eff_row.addWidget(eff_lbl)
        eff_row.addWidget(self.effect_combo, 1)
        fx_layout.addLayout(eff_row)

        bri_row = QHBoxLayout()
        bri_lbl = QLabel("Brilho")
        bri_lbl.setFixedWidth(85)
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.valueChanged.connect(self._on_bri_slider)
        self.brightness_spin = self._make_spin(0, 100, "%")
        self.brightness_spin.valueChanged.connect(self._on_bri_spin)
        bri_row.addWidget(bri_lbl)
        bri_row.addWidget(self.brightness_slider, 1)
        bri_row.addWidget(self.brightness_spin)
        fx_layout.addLayout(bri_row)

        spd_row = QHBoxLayout()
        self.speed_lbl = QLabel("Velocidade")
        self.speed_lbl.setFixedWidth(85)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(0, 100)
        self.speed_slider.valueChanged.connect(self._on_spd_slider)
        self.speed_spin = self._make_spin(0, 100, "%")
        self.speed_spin.valueChanged.connect(self._on_spd_spin)
        spd_row.addWidget(self.speed_lbl)
        spd_row.addWidget(self.speed_slider, 1)
        spd_row.addWidget(self.speed_spin)
        fx_layout.addLayout(spd_row)

        layout.addWidget(self.fx_card)

        # Card: Cor (uniforme com o separador do teclado)
        self.color_card = QFrame()
        self.color_card.setObjectName("card")
        color_layout = QVBoxLayout(self.color_card)
        color_layout.setContentsMargins(16, 14, 16, 16)
        color_layout.setSpacing(12)

        color_top = QHBoxLayout()
        color_header = QLabel("Cor")
        color_header.setObjectName("sectionTitle")
        self.color_note_lbl = QLabel("")
        self.color_note_lbl.setObjectName("subtitle")
        color_top.addWidget(color_header)
        color_top.addStretch()
        color_top.addWidget(self.color_note_lbl)
        color_layout.addLayout(color_top)

        picker_row = QHBoxLayout()
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(36, 36)
        self.picker_btn = QPushButton("Escolher Cor...")
        self.picker_btn.clicked.connect(self._open_color_dialog)
        self.sync_to_kb_btn = QPushButton("Copiar → Teclado")
        self.sync_to_kb_btn.setToolTip("Aplicar esta cor no teclado Keychron V6 Max")
        self.sync_to_kb_btn.clicked.connect(
            lambda: self.syncColorRequested.emit(self.state.color)
        )
        picker_row.addWidget(self.color_swatch)
        picker_row.addWidget(self.picker_btn)
        picker_row.addWidget(self.sync_to_kb_btn)
        picker_row.addStretch()
        color_layout.addLayout(picker_row)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(6)
        for name, rgb, hex_color in MOUSE_COLOR_PRESETS:
            btn = QPushButton()
            btn.setToolTip(name)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hex_color}; border-radius: 14px; "
                f"border: 2px solid #2b303b; padding: 0; }}"
                f"QPushButton:hover {{ border-color: #ffffff; }}"
            )
            btn.clicked.connect(lambda _, c=rgb: self._apply_color(c))
            presets_row.addWidget(btn)
        presets_row.addStretch()
        color_layout.addLayout(presets_row)

        layout.addWidget(self.color_card)

        # Card: Desempenho (DPI, Polling Rate, LOD, Debounce)
        self.perf_card = QFrame()
        self.perf_card.setObjectName("card")
        perf_layout = QVBoxLayout(self.perf_card)
        perf_layout.setContentsMargins(16, 12, 16, 14)
        perf_layout.setSpacing(10)

        perf_top = QHBoxLayout()
        perf_header = QLabel("Desempenho (DPI & Polling)")
        perf_header.setObjectName("sectionTitle")
        self.apply_perf_btn = QPushButton("Aplicar Desempenho")
        self.apply_perf_btn.setObjectName("primaryBtn")
        self.apply_perf_btn.clicked.connect(self._apply_performance)
        perf_top.addWidget(perf_header)
        perf_top.addStretch()
        perf_top.addWidget(self.apply_perf_btn)
        perf_layout.addLayout(perf_top)

        perf_row1 = QHBoxLayout()
        perf_row1.setSpacing(8)
        poll_lbl = QLabel("Polling")
        self.polling_combo = QComboBox()
        for rate in sorted(POLLING_RATES):
            self.polling_combo.addItem(f"{rate} Hz", rate)

        lod_lbl = QLabel("LOD")
        self.lod_combo = QComboBox()
        for lod_val, lod_text in LIFT_OFF_OPTIONS:
            self.lod_combo.addItem(lod_text, lod_val)

        deb_lbl = QLabel("Debounce")
        self.debounce_spin = self._make_spin(0, 16, " ms")
        self.debounce_spin.setSingleStep(2)

        perf_row1.addWidget(poll_lbl)
        perf_row1.addWidget(self.polling_combo, 1)
        perf_row1.addWidget(lod_lbl)
        perf_row1.addWidget(self.lod_combo)
        perf_row1.addWidget(deb_lbl)
        perf_row1.addWidget(self.debounce_spin)
        perf_layout.addLayout(perf_row1)

        dpi_row = QHBoxLayout()
        dpi_row.setSpacing(6)
        dpi_lbl = QLabel("DPI")
        dpi_lbl.setFixedWidth(30)
        dpi_row.addWidget(dpi_lbl)

        self._dpi_stage_btns: list[QPushButton] = []
        self._dpi_stage_spins: list[QSpinBox] = []
        for idx in range(4):
            stage_box = QVBoxLayout()
            stage_box.setSpacing(4)
            btn = QPushButton(f"Nível {idx + 1}")
            btn.setCheckable(True)
            btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
            btn.clicked.connect(lambda _, i=idx: self._select_dpi_stage(i))
            spin = QSpinBox()
            spin.setObjectName("valueBadge")
            spin.setRange(DPI_MIN, DPI_MAX)
            spin.setSingleStep(DPI_STEP)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setKeyboardTracking(False)
            stage_box.addWidget(btn)
            stage_box.addWidget(spin)
            dpi_row.addLayout(stage_box, 1)
            self._dpi_stage_btns.append(btn)
            self._dpi_stage_spins.append(spin)
        perf_layout.addLayout(dpi_row)

        layout.addWidget(self.perf_card)

        self.feedback_lbl = QLabel("")
        self.feedback_lbl.setObjectName("subtitle")
        layout.addWidget(self.feedback_lbl)

    def _load_ui_from_state(self) -> None:
        self._syncing = True
        try:
            idx = self.effect_combo.findData(self.state.effect)
            if idx >= 0:
                self.effect_combo.setCurrentIndex(idx)
            self.brightness_slider.setValue(self.state.brightness)
            self.brightness_spin.setValue(self.state.brightness)
            self.speed_slider.setValue(self.state.speed)
            self.speed_spin.setValue(self.state.speed)
            self._update_swatch()
            self._update_power_button(self.state.effect > 0 and self.state.brightness > 0)
            self._update_dependent_controls(self.state.effect)
            self._render_battery(self.state.battery_pct)
            self._update_battery_note()

            p_idx = self.polling_combo.findData(self.state.polling_rate)
            if p_idx >= 0:
                self.polling_combo.setCurrentIndex(p_idx)
            l_idx = self.lod_combo.findData(self.state.lift_off)
            if l_idx >= 0:
                self.lod_combo.setCurrentIndex(l_idx)
            self.debounce_spin.setValue(self.state.debounce)
            for i in range(4):
                dpi_val = self.state.dpi_stages[i][0] if i < len(self.state.dpi_stages) else 800
                self._dpi_stage_spins[i].setValue(dpi_val)
            self._select_dpi_stage(min(self.state.active_dpi_stage, 3))
        finally:
            self._syncing = False

    def _open_battery_history(self) -> None:
        dlg = BatteryHistoryDialog(
            "mouse",
            "Glorious Model I 2",
            current_pct=self.state.battery_pct,
            is_charging=self.device.conn_type == "usb",
            parent=self,
        )
        dlg.exec()
        self._update_battery_note()

    def _update_battery_note(self) -> None:
        summary = format_battery_summary(
            "mouse", self.state.battery_pct, self.device.conn_type == "usb"
        )
        if summary:
            self.battery_note_lbl.setText(summary)
        else:
            self.battery_note_lbl.setText(
                "Atualiza automaticamente em 2.4G quando mexes no rato."
            )

    def _select_dpi_stage(self, idx: int) -> None:
        self.state.active_dpi_stage = idx
        for i, btn in enumerate(self._dpi_stage_btns):
            active = i == idx
            btn.blockSignals(True)
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(
                    "padding: 4px 6px; font-size: 11px; background-color: #3b76f6; "
                    "border-color: #4f8cff; color: #ffffff;"
                )
            else:
                btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
            btn.blockSignals(False)

    def _apply_performance(self) -> None:
        stages: list[tuple[int, tuple[int, int, int]]] = []
        for i in range(4):
            dpi = int(self._dpi_stage_spins[i].value())
            dpi = max(DPI_MIN, min(DPI_MAX, round(dpi / DPI_STEP) * DPI_STEP))
            self._dpi_stage_spins[i].setValue(dpi)
            color = self.state.dpi_stages[i][1] if i < len(self.state.dpi_stages) else (255, 255, 255)
            stages.append((dpi, color))

        self.state.dpi_stages = stages
        self.state.polling_rate = int(self.polling_combo.currentData())
        self.state.lift_off = int(self.lod_combo.currentData())
        self.state.debounce = int(self.debounce_spin.value())
        self._persist_state()

        if not self.device.is_connected:
            self.feedback_lbl.setText("Rato desligado — desempenho guardado localmente")
            return
        try:
            self.device.set_performance(
                dpi_stages=self.state.dpi_stages,
                active_stage=self.state.active_dpi_stage,
                polling_rate=self.state.polling_rate,
                lift_off=self.state.lift_off,
                debounce=self.state.debounce,
            )
            active_dpi = self.state.dpi_stages[self.state.active_dpi_stage][0]
            self.feedback_lbl.setText(
                f"Desempenho aplicado ({active_dpi} DPI · {self.state.polling_rate} Hz) ✓"
            )
        except Exception as exc:
            self.feedback_lbl.setText(f"Erro ao aplicar desempenho: {exc}")

    def get_profile_dict(self) -> dict:
        return {
            "effect": self.state.effect,
            "brightness": self.state.brightness,
            "speed": self.state.speed,
            "color": list(self.state.color),
        }

    def apply_profile(self, mprof: dict) -> bool:
        if not isinstance(mprof, dict):
            return False
        self.state.effect = int(mprof.get("effect", self.state.effect))
        self.state.brightness = int(mprof.get("brightness", self.state.brightness))
        self.state.speed = int(mprof.get("speed", self.state.speed))
        raw_col = mprof.get("color", list(self.state.color))
        self.state.color = (int(raw_col[0]), int(raw_col[1]), int(raw_col[2]))
        if self.state.effect > 0:
            self._last_active_effect = self.state.effect
        self._load_ui_from_state()
        self._send_lighting()
        return self.device.is_connected

    def _render_battery(self, pct: int | None) -> None:
        if pct is None:
            self.battery_bar.setValue(0)
            self.battery_pct_lbl.setText("--%")
            return
        self.battery_bar.setValue(pct)
        self.battery_pct_lbl.setText(f"{pct}%")
        color = "#4cd964" if pct > 50 else ("#ffd60a" if pct > 20 else "#ff453a")
        self.battery_bar.setStyleSheet(
            f"QProgressBar {{ background-color: #252933; border: 1px solid #343a47; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}"
        )

    def _update_swatch(self) -> None:
        r, g, b = self.state.color
        self.color_swatch.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border-radius: 8px; border: 1px solid #3e4554;"
        )

    def _update_power_button(self, is_on: bool) -> None:
        self.power_btn.blockSignals(True)
        self.power_btn.setChecked(is_on)
        self.power_btn.setText("Luzes: ON" if is_on else "Luzes: OFF")
        self.power_btn.blockSignals(False)

    def _update_dependent_controls(self, eff_id: int) -> None:
        uses_color = eff_id in MOUSE_COLOR_EFFECTS
        uses_speed = eff_id not in (0, 4)
        self.speed_slider.setEnabled(uses_speed)
        self.speed_spin.setEnabled(uses_speed)
        self.speed_lbl.setEnabled(uses_speed)
        self.picker_btn.setEnabled(uses_color)
        self.color_note_lbl.setText("" if uses_color else "(não aplicável neste efeito)")

    def connect_mouse(self) -> None:
        self.feedback_lbl.setText("")
        try:
            path = self.device.connect()
        except Exception as exc:
            self.fx_card.setEnabled(False)
            self.color_card.setEnabled(False)
            self.perf_card.setEnabled(False)
            self.power_btn.setEnabled(False)
            self.status_lbl.setText(f"Desligado ({exc})")
            self.status_lbl.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            self.charging_lbl.setText("")
            return

        self.fx_card.setEnabled(True)
        self.color_card.setEnabled(True)
        self.perf_card.setEnabled(True)
        self.power_btn.setEnabled(True)
        self.status_lbl.setText(f"Ligado via {self.device.connection_label} ({path})")
        self.status_lbl.setStyleSheet("color: #4cd964; font-size: 12px;")
        self._refresh_charging_label()
        self._update_battery_note()
        cfg = load_auto_config()
        self.check_battery_saver(
            bool(cfg.get("battery_saver_enabled", False)),
            int(cfg.get("battery_saver_threshold", 20)),
            int(cfg.get("battery_saver_brightness_pct", 15)),
        )

    def _refresh_charging_label(self) -> None:
        if self.device.conn_type == "usb":
            self.charging_lbl.setText("⚡ A carregar por USB-C")
            self.charging_lbl.setStyleSheet("color: #ffd60a; font-size: 12px;")
        elif self._saver_active:
            self.charging_lbl.setText("Sem fios (2.4G) · Poupança RGB")
            self.charging_lbl.setStyleSheet("color: #ffd60a; font-size: 12px;")
        else:
            self.charging_lbl.setText("Sem fios (2.4G)")
            self.charging_lbl.setStyleSheet("color: #9aa3b2; font-size: 12px;")

    def check_battery_saver(
        self, enabled: bool, threshold: int = 20, saver_bri: int = 15
    ) -> None:
        pct = self.state.battery_pct
        should_save = (
            enabled
            and self.device.is_connected
            and self.device.conn_type == "receiver"
            and pct is not None
            and (pct <= threshold or (self._saver_active and pct < threshold + 5))
        )
        if should_save and not self._saver_active:
            self._saver_active = True
            self._pre_saver_brightness = self.state.brightness
            target_bri = min(self.state.brightness, saver_bri)
            if self.state.effect > 0 and target_bri < self.state.brightness:
                try:
                    self.device.set_lighting(
                        self.state.effect, target_bri, self.state.speed, self.state.color
                    )
                except Exception:
                    pass
            self._refresh_charging_label()
        elif not should_save and self._saver_active:
            self._saver_active = False
            restore_bri = (
                self._pre_saver_brightness
                if self._pre_saver_brightness is not None
                else self.state.brightness
            )
            self._pre_saver_brightness = None
            if self.device.is_connected and self.state.effect > 0:
                try:
                    self.device.set_lighting(
                        self.state.effect, restore_bri, self.state.speed, self.state.color
                    )
                except Exception:
                    pass
            self._refresh_charging_label()

    @staticmethod
    def _get_state_mtime() -> float:
        try:
            return os.path.getmtime(MOUSE_STATE_PATH) if os.path.exists(MOUSE_STATE_PATH) else 0.0
        except OSError:
            return 0.0

    def _persist_state(self) -> None:
        if self.state.effect > 0:
            self.state.last_active_effect = self.state.effect
        save_mouse_state(self.state)
        self._state_mtime = self._get_state_mtime()

    def _sync_external_state(self) -> None:
        """Recarrega mouse_state.json se o serviço de automação o tiver alterado."""
        mtime = self._get_state_mtime()
        if mtime > 0 and mtime != self._state_mtime:
            self._state_mtime = mtime
            self.state = load_mouse_state()
            self._last_active_effect = (
                self.state.effect if self.state.effect > 0 else self.state.last_active_effect
            )
            self._load_ui_from_state()

    def _poll_battery(self) -> None:
        self._sync_external_state()
        if not self.device.is_connected:
            self.connect_mouse()
            return
        pct = self.device.poll_battery_frame()
        if pct is not None:
            self.state.battery_pct = pct
            self._persist_state()
            record_battery("mouse", pct, self.device.conn_type == "usb")
            self._render_battery(pct)
            self._update_battery_note()
            self._check_low_battery_alert(pct)
            cfg = load_auto_config()
            self.check_battery_saver(
                bool(cfg.get("battery_saver_enabled", False)),
                int(cfg.get("battery_saver_threshold", 20)),
                int(cfg.get("battery_saver_brightness_pct", 15)),
            )
            self.batteryUpdated.emit()

    def _check_low_battery_alert(self, pct: int) -> None:
        if self.device.conn_type == "usb":
            self._low_bat_alerted = False
            if pct == 100 and not self._full_bat_alerted:
                self._full_bat_alerted = True
                notify_full_battery("Glorious Model I 2")
            elif pct < 98:
                self._full_bat_alerted = False
            return

        self._full_bat_alerted = False
        if pct >= 25:
            self._low_bat_alerted = False
            return
        if pct <= 20 and not self._low_bat_alerted:
            self._low_bat_alerted = True
            notify_low_battery("Glorious Model I 2", pct)

    def _send_lighting(self) -> None:
        self._persist_state()
        if not self.device.is_connected:
            return
        try:
            self.device.set_lighting(
                effect_id=self.state.effect,
                brightness_pct=self.state.brightness,
                speed_pct=self.state.speed,
                rgb=self.state.color,
            )
            self.feedback_lbl.setText("Aplicado ao rato ✓")
        except Exception as exc:
            self.fx_card.setEnabled(False)
            self.color_card.setEnabled(False)
            self.power_btn.setEnabled(False)
            self.status_lbl.setText(f"Ligação perdida: {exc}")
            self.status_lbl.setStyleSheet("color: #ff6b6b; font-size: 12px;")

    def _on_power_toggled(self, checked: bool) -> None:
        self._update_power_button(checked)
        target_eff = self._last_active_effect if checked else 0
        if checked and self.state.brightness == 0:
            self.state.brightness = 60
            self._syncing = True
            self.brightness_slider.setValue(60)
            self.brightness_spin.setValue(60)
            self._syncing = False
        self.state.effect = target_eff
        self._syncing = True
        idx = self.effect_combo.findData(target_eff)
        if idx >= 0:
            self.effect_combo.setCurrentIndex(idx)
        self._update_dependent_controls(target_eff)
        self._syncing = False
        self._send_lighting()

    def _on_effect_changed(self) -> None:
        if self._syncing:
            return
        eff_id = int(self.effect_combo.currentData())
        self.state.effect = eff_id
        if eff_id > 0:
            self._last_active_effect = eff_id
        self._update_power_button(eff_id > 0 and self.state.brightness > 0)
        self._update_dependent_controls(eff_id)
        self._send_lighting()

    def _on_bri_slider(self, val: int) -> None:
        if self._syncing:
            return
        self.brightness_spin.blockSignals(True)
        self.brightness_spin.setValue(val)
        self.brightness_spin.blockSignals(False)
        self.state.brightness = val
        self._update_power_button(self.state.effect > 0 and val > 0)
        self._apply_timer.start()

    def _on_bri_spin(self, val: int) -> None:
        if self._syncing:
            return
        self.brightness_slider.blockSignals(True)
        self.brightness_slider.setValue(val)
        self.brightness_slider.blockSignals(False)
        self.state.brightness = val
        self._update_power_button(self.state.effect > 0 and val > 0)
        self._send_lighting()

    def _on_spd_slider(self, val: int) -> None:
        if self._syncing:
            return
        self.speed_spin.blockSignals(True)
        self.speed_spin.setValue(val)
        self.speed_spin.blockSignals(False)
        self.state.speed = val
        self._apply_timer.start()

    def _on_spd_spin(self, val: int) -> None:
        if self._syncing:
            return
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(val)
        self.speed_slider.blockSignals(False)
        self.state.speed = val
        self._send_lighting()

    def _apply_color(self, rgb: tuple[int, int, int]) -> None:
        self.state.color = rgb
        self._update_swatch()
        self._send_lighting()

    def _open_color_dialog(self) -> None:
        r, g, b = self.state.color
        chosen = QColorDialog.getColor(QColor(r, g, b), self, "Escolher Cor do Rato")
        if chosen.isValid():
            self._apply_color((chosen.red(), chosen.green(), chosen.blue()))

    def close_device(self) -> None:
        self._battery_timer.stop()
        self.device.close()
