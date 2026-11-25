"""Separador de controlo dos auscultadores REDMI Headphones Neo (Bluetooth RFCOMM)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from automation import load_config as load_auto_config
from automation import notify_low_battery
from battery_dialog import BatteryHistoryDialog
from battery_log import format_battery_summary, record_battery
from redmi_bt import (
    ANC_MODES,
    EQ_PRESETS,
    QUICK_PROFILES,
    HeadphonesState,
    RedmiHeadphonesDevice,
    find_connected_redmi_mac,
    get_audio_profiles,
    load_headphones_state,
    read_bluez_battery,
    save_headphones_state,
    set_audio_profile,
)


class HeadphonesTab(QWidget):
    """Painel de bateria, perfis rápidos, ANC, EQ e áudio PipeWire dos REDMI Headphones Neo."""

    batteryUpdated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device = RedmiHeadphonesDevice()
        self.state: HeadphonesState = load_headphones_state()
        self._syncing = False
        self._low_bat_alerted = False
        self._anc_buttons: dict[int, QPushButton] = {}

        self._battery_timer = QTimer(self)
        self._battery_timer.setInterval(15000)
        self._battery_timer.timeout.connect(self._poll_battery_passive)

        self._build_ui()
        self._load_ui_from_state()
        self.connect_and_sync()
        self._battery_timer.start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        # Cabeçalho
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("REDMI Headphones Neo")
        title_lbl.setObjectName("title")
        self.status_lbl = QLabel("A procurar auscultadores...")
        self.status_lbl.setObjectName("subtitle")
        title_box.addWidget(title_lbl)
        title_box.addWidget(self.status_lbl)

        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.connect_and_sync)

        header.addLayout(title_box, 1)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        # Cartão de Bateria
        bat_card = QFrame()
        bat_card.setObjectName("card")
        bat_layout = QVBoxLayout(bat_card)
        bat_layout.setContentsMargins(16, 14, 16, 14)
        bat_layout.setSpacing(8)

        bat_top = QHBoxLayout()
        bat_title = QLabel("BATERIA")
        bat_title.setObjectName("sectionTitle")
        self.bat_pct_lbl = QLabel("--%")
        self.bat_pct_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #30d158;")
        self.history_btn = QPushButton("Histórico")
        self.history_btn.clicked.connect(self._open_battery_history)
        bat_top.addWidget(bat_title)
        bat_top.addStretch()
        bat_top.addWidget(self.bat_pct_lbl)
        bat_top.addWidget(self.history_btn)

        self.bat_bar = QProgressBar()
        self.bat_bar.setRange(0, 100)
        self.bat_bar.setValue(0)
        self.bat_bar.setTextVisible(False)
        self.bat_bar.setFixedHeight(8)
        self.bat_bar.setStyleSheet(
            "QProgressBar { background: #252933; border-radius: 4px; }"
            "QProgressBar::chunk { background: #30d158; border-radius: 4px; }"
        )

        self.bat_summary_lbl = QLabel("Sem registos ainda")
        self.bat_summary_lbl.setObjectName("subtitle")

        bat_layout.addLayout(bat_top)
        bat_layout.addWidget(self.bat_bar)
        bat_layout.addWidget(self.bat_summary_lbl)
        layout.addWidget(bat_card)

        # Cartão de Perfis Rápidos (ANC + EQ)
        quick_card = QFrame()
        quick_card.setObjectName("card")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(16, 12, 16, 12)
        quick_layout.setSpacing(8)

        quick_title = QLabel("PERFIS RÁPIDOS")
        quick_title.setObjectName("sectionTitle")
        quick_layout.addWidget(quick_title)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        self._quick_buttons: list[QPushButton] = []
        for pname, (anc_m, eq_p) in QUICK_PROFILES.items():
            btn = QPushButton(pname)
            btn.clicked.connect(
                lambda _, n=pname, a=anc_m, e=eq_p: self._apply_quick_profile(n, a, e)
            )
            self._quick_buttons.append(btn)
            quick_row.addWidget(btn)
        quick_layout.addLayout(quick_row)
        layout.addWidget(quick_card)

        # Cartão de Controlo de Ruído (ANC)
        anc_card = QFrame()
        anc_card.setObjectName("card")
        anc_layout = QVBoxLayout(anc_card)
        anc_layout.setContentsMargins(16, 14, 16, 14)
        anc_layout.setSpacing(10)

        anc_title = QLabel("CONTROLO DE RUÍDO (ANC)")
        anc_title.setObjectName("sectionTitle")
        anc_layout.addWidget(anc_title)

        anc_row = QHBoxLayout()
        anc_row.setSpacing(8)
        for mode_id, label in ANC_MODES.items():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, m=mode_id: self.set_anc_mode(m))
            self._anc_buttons[mode_id] = btn
            anc_row.addWidget(btn)
        anc_layout.addLayout(anc_row)
        layout.addWidget(anc_card)

        # Cartão de Equalizador (EQ) e Ligação Dupla
        eq_card = QFrame()
        eq_card.setObjectName("card")
        eq_layout = QVBoxLayout(eq_card)
        eq_layout.setContentsMargins(16, 14, 16, 14)
        eq_layout.setSpacing(10)

        eq_top = QHBoxLayout()
        eq_title = QLabel("EQUALIZADOR (EQ)")
        eq_title.setObjectName("sectionTitle")
        self.dual_cb = QCheckBox("Ligação dupla (Dual-Device)")
        self.dual_cb.toggled.connect(self._on_dual_toggled)
        eq_top.addWidget(eq_title)
        eq_top.addStretch()
        eq_top.addWidget(self.dual_cb)

        self.eq_combo = QComboBox()
        for preset_id, label in EQ_PRESETS.items():
            self.eq_combo.addItem(label, preset_id)
        self.eq_combo.currentIndexChanged.connect(self._on_eq_changed)

        eq_layout.addLayout(eq_top)
        eq_layout.addWidget(self.eq_combo)
        layout.addWidget(eq_card)

        # Cartão de Perfil de Áudio Linux (PipeWire / BlueZ)
        audio_card = QFrame()
        audio_card.setObjectName("card")
        audio_layout = QVBoxLayout(audio_card)
        audio_layout.setContentsMargins(16, 12, 16, 14)
        audio_layout.setSpacing(8)

        audio_title = QLabel("ÁUDIO LINUX (PIPEWIRE / CODEC)")
        audio_title.setObjectName("sectionTitle")
        self.audio_combo = QComboBox()
        self.audio_combo.currentIndexChanged.connect(self._on_audio_profile_changed)

        audio_layout.addWidget(audio_title)
        audio_layout.addWidget(self.audio_combo)
        layout.addWidget(audio_card)

        layout.addStretch()

        # Rodapé
        self.feedback_lbl = QLabel("")
        self.feedback_lbl.setObjectName("subtitle")
        self.feedback_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.feedback_lbl)

    def _update_battery_ui(self, pct: int | None) -> None:
        if pct is None:
            self.bat_pct_lbl.setText("--%")
            self.bat_bar.setValue(0)
        else:
            self.bat_pct_lbl.setText(f"{pct}%")
            self.bat_bar.setValue(pct)
            color = "#30d158" if pct > 25 else ("#ffd60a" if pct > 15 else "#ff453a")
            self.bat_pct_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {color};")
            self.bat_bar.setStyleSheet(
                "QProgressBar { background: #252933; border-radius: 4px; }"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}"
            )
            self._check_low_battery_alert(pct)
        self.bat_summary_lbl.setText(format_battery_summary("redmi_neo", pct, False))

    def _check_low_battery_alert(self, pct: int) -> None:
        cfg = load_auto_config()
        if not cfg.get("low_battery_alert", True):
            return
        threshold = int(cfg.get("low_battery_threshold", 20))
        if pct <= threshold and not self._low_bat_alerted:
            self._low_bat_alerted = True
            notify_low_battery("REDMI Headphones Neo", pct)
        elif pct > threshold + 5:
            self._low_bat_alerted = False

    def _update_anc_buttons(self, active_mode: int) -> None:
        for mode_id, btn in self._anc_buttons.items():
            is_active = mode_id == active_mode
            btn.blockSignals(True)
            btn.setChecked(is_active)
            btn.setStyleSheet(
                "background-color: #365999; border: 1px solid #4f8cff; color: #ffffff;"
                if is_active
                else ""
            )
            btn.blockSignals(False)

    def _load_ui_from_state(self) -> None:
        self._syncing = True
        self._update_battery_ui(self.state.battery_pct)
        self._update_anc_buttons(self.state.anc_mode)
        idx = self.eq_combo.findData(self.state.eq_preset)
        if idx >= 0:
            self.eq_combo.setCurrentIndex(idx)
        if self.state.dual_connection is not None:
            self.dual_cb.setChecked(self.state.dual_connection)
        self._syncing = False

    def _set_rfcomm_controls_enabled(self, enabled: bool) -> None:
        for btn in self._anc_buttons.values():
            btn.setEnabled(enabled)
        for btn in self._quick_buttons:
            btn.setEnabled(enabled)
        self.eq_combo.setEnabled(enabled)
        self.dual_cb.setEnabled(enabled)

    def _refresh_audio_profiles(self, mac: str | None) -> None:
        self._syncing = True
        self.audio_combo.clear()
        if not mac:
            self.audio_combo.setEnabled(False)
            self._syncing = False
            return
        active, profiles = get_audio_profiles(mac)
        if not profiles:
            self.audio_combo.setEnabled(False)
            self._syncing = False
            return
        self.audio_combo.setEnabled(True)
        for pid, label in profiles:
            self.audio_combo.addItem(label, pid)
        if active:
            idx = self.audio_combo.findData(active)
            if idx >= 0:
                self.audio_combo.setCurrentIndex(idx)
        self._syncing = False

    def _poll_battery_passive(self) -> None:
        mac = find_connected_redmi_mac()
        if not mac:
            if self.device.connected:
                self.device.connected = False
                self.status_lbl.setText("Auscultadores desligados")
                self._set_rfcomm_controls_enabled(False)
                self._refresh_audio_profiles(None)
            return

        self.device.mac = mac
        self.device.connected = True
        pct = read_bluez_battery(mac)
        if pct is not None and pct != self.state.battery_pct:
            self.state.battery_pct = pct
            save_headphones_state(self.state)
            record_battery("redmi_neo", pct, False)
            self._update_battery_ui(pct)
            self.batteryUpdated.emit()

    def connect_and_sync(self) -> None:
        mac = find_connected_redmi_mac()
        if not mac:
            self.device.connected = False
            self.status_lbl.setText("Auscultadores desligados")
            self._set_rfcomm_controls_enabled(False)
            self._refresh_audio_profiles(None)
            self.feedback_lbl.setText("Liga os REDMI Headphones Neo por Bluetooth")
            return

        self.state.mac = mac
        self._refresh_audio_profiles(mac)
        name, bat = self.device.get_device_info()
        if name:
            self.state.name = name

        if self.device.rfcomm_busy:
            self.status_lbl.setText(f"Ligado ({mac}) · Canal ocupado pelo telemóvel")
            self._set_rfcomm_controls_enabled(False)
            self.feedback_lbl.setText("Fecha a app Xiaomi Earbuds no telemóvel para controlar aqui")
        else:
            self.status_lbl.setText(f"Ligado · Bluetooth ({mac})")
            self._set_rfcomm_controls_enabled(True)
            anc = self.device.get_anc_mode()
            if anc is not None:
                self.state.anc_mode = anc
            eq = self.device.get_eq_preset()
            if eq is not None:
                self.state.eq_preset = eq
                if self.eq_combo.findData(eq) < 0:
                    self.eq_combo.addItem(f"Perfil (0x{eq:02x})", eq)
            dual = self.device.get_dual_connection()
            if dual is not None:
                self.state.dual_connection = dual
            self.feedback_lbl.setText("Estado sincronizado ✓")

        if bat is not None:
            self.state.battery_pct = bat
            record_battery("redmi_neo", bat, False)

        save_headphones_state(self.state)
        self._load_ui_from_state()
        self.batteryUpdated.emit()

    def set_anc_mode(self, mode_id: int) -> None:
        if self._syncing:
            return
        if self.device.set_anc_mode(mode_id):
            self.state.anc_mode = mode_id
            save_headphones_state(self.state)
            self._update_anc_buttons(mode_id)
            self.feedback_lbl.setText(f"ANC: {ANC_MODES.get(mode_id, mode_id)} ✓")
        elif self.device.rfcomm_busy:
            self._update_anc_buttons(self.state.anc_mode)
            self.feedback_lbl.setText("Canal ocupado pelo telemóvel (dual-device)")
        else:
            self._update_anc_buttons(self.state.anc_mode)
            self.feedback_lbl.setText("Não foi possível alterar o ANC")

    def _on_eq_changed(self, _index: int) -> None:
        if self._syncing:
            return
        preset = self.eq_combo.currentData()
        if preset is None:
            return
        preset_id = int(preset)
        if self.device.set_eq_preset(preset_id):
            self.state.eq_preset = preset_id
            save_headphones_state(self.state)
            self.feedback_lbl.setText(f"EQ: {self.eq_combo.currentText()} ✓")
        elif self.device.rfcomm_busy:
            self.feedback_lbl.setText("Canal ocupado pelo telemóvel (dual-device)")
        else:
            self.feedback_lbl.setText("Não foi possível alterar o EQ")

    def _apply_quick_profile(self, name: str, anc_mode: int, eq_preset: int) -> None:
        ok_anc, ok_eq = self.device.apply_anc_and_eq(anc_mode, eq_preset)
        if ok_anc or ok_eq:
            if ok_anc:
                self.state.anc_mode = anc_mode
            if ok_eq:
                self.state.eq_preset = eq_preset
            save_headphones_state(self.state)
            self._load_ui_from_state()
            self.feedback_lbl.setText(f"Perfil aplicado: {name} ✓")
        elif self.device.rfcomm_busy:
            self.feedback_lbl.setText("Canal ocupado pelo telemóvel (dual-device)")
        else:
            self.feedback_lbl.setText("Não foi possível aplicar o perfil")

    def _on_dual_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        if self.device.set_dual_connection(checked):
            self.state.dual_connection = checked
            save_headphones_state(self.state)
            estado = "ativada" if checked else "desativada"
            self.feedback_lbl.setText(f"Ligação dupla {estado} ✓")
        else:
            self._syncing = True
            self.dual_cb.setChecked(bool(self.state.dual_connection))
            self._syncing = False
            self.feedback_lbl.setText("Não foi possível alterar ligação dupla")

    def _on_audio_profile_changed(self, _index: int) -> None:
        if self._syncing or not self.state.mac:
            return
        pid = self.audio_combo.currentData()
        if not pid:
            return
        if set_audio_profile(self.state.mac, str(pid)):
            self.feedback_lbl.setText(f"Áudio: {self.audio_combo.currentText()} ✓")
        else:
            self.feedback_lbl.setText("Erro ao alterar perfil PipeWire")

    def _open_battery_history(self) -> None:
        dlg = BatteryHistoryDialog(
            "redmi_neo",
            "REDMI Headphones Neo",
            current_pct=self.state.battery_pct,
            is_charging=False,
            parent=self,
        )
        dlg.exec()

    def close_device(self) -> None:
        self._battery_timer.stop()
