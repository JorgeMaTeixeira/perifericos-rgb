#!/usr/bin/env python3
"""Aplicação desktop em tema escuro para controlar o RGB do Keychron V6 Max."""

from __future__ import annotations

from collections import deque
import colorsys
import json
import os
import sys

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from automation import (
    get_sun_times,
    is_service_active,
    load_config as load_auto_config,
    notify_full_battery,
    notify_low_battery,
    save_config as save_auto_config,
    set_service_enabled,
)
from battery_dialog import BatteryHistoryDialog
from battery_log import format_battery_summary, record_battery
from mouse_tab import MouseTab
from via_hid import EFFECTS, KeychronRGBDevice

PROFILES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.json")
KB_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keyboard_state.json")

DEFAULT_PROFILES: dict[str, dict[str, int]] = {
    "Trabalho (Branco 60%)": {"effect": 1, "brightness": 153, "speed": 127, "hue": 0, "saturation": 0},
    "Noite (Âmbar 20%)": {"effect": 1, "brightness": 51, "speed": 127, "hue": 21, "saturation": 255},
    "Arco-íris": {"effect": 8, "brightness": 178, "speed": 127, "hue": 16, "saturation": 255},
    "Gaming (Reativo Vermelho)": {"effect": 18, "brightness": 215, "speed": 150, "hue": 0, "saturation": 255},
}

COLOR_PRESETS: list[tuple[str, int, int, str]] = [
    ("Branco", 0, 0, "#ffffff"),
    ("Vermelho", 0, 255, "#ff3b30"),
    ("Laranja", 21, 255, "#ff7b00"),
    ("Amarelo", 42, 255, "#ffd60a"),
    ("Verde", 85, 255, "#30d158"),
    ("Ciano", 128, 255, "#32d4de"),
    ("Azul", 170, 255, "#0a84ff"),
    ("Roxo", 191, 255, "#bf5af2"),
    ("Rosa", 224, 255, "#ff375f"),
]

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #121418;
    color: #e6e9ef;
    font-family: "Inter", "Ubuntu", "Cantarell", sans-serif;
    font-size: 13px;
}
QFrame#card {
    background-color: #1b1e25;
    border: 1px solid #2b303b;
    border-radius: 12px;
}
QLabel#title {
    font-size: 18px;
    font-weight: 700;
    color: #ffffff;
}
QLabel#subtitle {
    font-size: 12px;
    color: #9aa3b2;
}
QLabel#sectionTitle {
    font-size: 12px;
    font-weight: 600;
    color: #9aa3b2;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
QSpinBox#valueBadge {
    background-color: #252933;
    border: 1px solid #343a47;
    border-radius: 6px;
    padding: 3px 6px;
    font-weight: 600;
    color: #f0f3f8;
    min-width: 48px;
}
QSpinBox#valueBadge:focus {
    border-color: #4f8cff;
}
QSpinBox#valueBadge:disabled {
    color: #636b7a;
    border-color: #282c36;
}
QComboBox {
    background-color: #252933;
    border: 1px solid #343a47;
    border-radius: 8px;
    padding: 8px 12px;
    color: #f0f3f8;
    font-size: 13px;
}
QComboBox:hover {
    border-color: #4f8cff;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #1b1e25;
    border: 1px solid #343a47;
    selection-background-color: #365999;
    color: #f0f3f8;
    outline: 0;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2b303b;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #4f8cff;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #4f8cff;
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover {
    background: #e8f0ff;
}
QSlider#hueSlider::groove:horizontal {
    height: 8px;
    border-radius: 4px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0.00 #ff0000,
        stop: 0.17 #ffff00,
        stop: 0.33 #00ff00,
        stop: 0.50 #00ffff,
        stop: 0.67 #0000ff,
        stop: 0.83 #ff00ff,
        stop: 1.00 #ff0000
    );
}
QSlider#hueSlider::sub-page:horizontal {
    background: transparent;
}
QPushButton {
    background-color: #252933;
    border: 1px solid #343a47;
    border-radius: 8px;
    padding: 8px 14px;
    color: #e6e9ef;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #2f3441;
    border-color: #4b5263;
}
QPushButton:pressed {
    background-color: #1f232c;
}
QPushButton#primaryBtn {
    background-color: #3b76f6;
    border-color: #4f8cff;
    color: #ffffff;
}
QPushButton#primaryBtn:hover {
    background-color: #4f8cff;
}
QPushButton#primaryBtn:disabled, QPushButton:disabled {
    background-color: #1f222a;
    border-color: #282c36;
    color: #636b7a;
}
QPushButton#powerBtn {
    background-color: #2a2124;
    border-color: #5c333a;
    color: #ff8a8a;
}
QPushButton#powerBtn:checked {
    background-color: #1e3528;
    border-color: #2f7a4d;
    color: #6ce596;
}
QPushButton#powerBtn:hover {
    border-color: #7c8598;
}
QCheckBox {
    spacing: 8px;
    color: #d6dbe4;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3a4150;
    background-color: #252933;
}
QCheckBox::indicator:checked {
    background-color: #3b76f6;
    border-color: #4f8cff;
}
QLineEdit {
    background-color: #252933;
    border: 1px solid #343a47;
    border-radius: 6px;
    padding: 5px 8px;
    color: #f0f3f8;
    max-width: 68px;
}
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background-color: #1b1e25;
    border: 1px solid #2b303b;
    border-radius: 8px;
    padding: 8px 16px;
    margin-right: 6px;
    color: #9aa3b2;
    font-weight: 600;
}
QTabBar::tab:selected {
    background-color: #252933;
    border-color: #4f8cff;
    color: #ffffff;
}
QTabBar::tab:hover:!selected {
    color: #e6e9ef;
}
"""


def effect_uses_color(effect_id: int) -> bool:
    """Reproduz a regra showIf do JSON oficial do Keychron V6 Max."""
    return effect_id != 0 and (
        effect_id < 4 or effect_id == 18 or (effect_id > 17 and effect_id != 21)
    )


def qmk_hs_to_qcolor(hue: int, sat: int) -> QColor:
    r, g, b = colorsys.hsv_to_rgb(hue / 255.0, sat / 255.0, 1.0)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


def qcolor_to_qmk_hs(color: QColor) -> tuple[int, int]:
    h, s, _ = colorsys.rgb_to_hsv(color.redF(), color.greenF(), color.blueF())
    return round(h * 255), round(s * 255)


class KeychronRGBWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.device = KeychronRGBDevice()
        self._syncing = False
        self._last_active_effect = 1
        self._kb_battery_pct: int | None = self._load_kb_battery()
        self._kb_bat_window: deque[int] = deque(maxlen=5)
        self._kb_last_conn_mode: str = ""
        self._kb_low_bat_alerted = False
        self._kb_full_bat_alerted = False
        self._kb_saver_active = False
        self._kb_pre_saver_brightness: int | None = None
        self._last_tray_icon_key: tuple | None = None
        self._profiles: dict[str, dict] = self._load_profiles()

        self.setWindowTitle("Periféricos RGB")
        self.setMinimumWidth(510)
        self.setMaximumWidth(600)

        # Timers para evitar envio excessivo ao arrastar sliders
        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(25)
        self._brightness_timer.timeout.connect(self._flush_brightness)

        self._speed_timer = QTimer(self)
        self._speed_timer.setSingleShot(True)
        self._speed_timer.setInterval(25)
        self._speed_timer.timeout.connect(self._flush_speed)

        self._color_timer = QTimer(self)
        self._color_timer.setSingleShot(True)
        self._color_timer.setInterval(25)
        self._color_timer.timeout.connect(self._flush_color)

        self._kb_bat_timer = QTimer(self)
        self._kb_bat_timer.setInterval(10000)
        self._kb_bat_timer.timeout.connect(self._periodic_kb_check)

        self._build_ui()
        if self._kb_battery_pct is not None:
            self._render_kb_battery(self._kb_battery_pct)
        self._setup_tray()
        self.connect_and_sync()
        self._kb_bat_timer.start()

    @staticmethod
    def _make_value_spinbox(min_val: int, max_val: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName("valueBadge")
        spin.setRange(min_val, max_val)
        spin.setSuffix(suffix)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setKeyboardTracking(False)
        return spin

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer_layout = QVBoxLayout(root)
        outer_layout.setContentsMargins(18, 16, 18, 16)
        outer_layout.setSpacing(12)

        self.tabs = QTabWidget()
        kb_tab = QWidget()
        main_layout = QVBoxLayout(kb_tab)
        main_layout.setContentsMargins(0, 8, 0, 0)
        main_layout.setSpacing(12)

        # Cabeçalho
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("Keychron V6 Max RGB")
        title_lbl.setObjectName("title")
        self.status_lbl = QLabel("A procurar teclado...")
        self.status_lbl.setObjectName("subtitle")
        title_box.addWidget(title_lbl)
        title_box.addWidget(self.status_lbl)

        self.power_btn = QPushButton("Luzes: ON")
        self.power_btn.setObjectName("powerBtn")
        self.power_btn.setCheckable(True)
        self.power_btn.setChecked(True)
        self.power_btn.clicked.connect(self._on_power_toggled)

        self.reconnect_btn = QPushButton("Reconectar")
        self.reconnect_btn.clicked.connect(self.connect_and_sync)
        header_row.addLayout(title_box, 1)
        header_row.addWidget(self.power_btn)
        header_row.addWidget(self.reconnect_btn)
        main_layout.addLayout(header_row)

        # Card: Bateria do teclado (uniforme com o separador do rato)
        kb_bat_card = QFrame()
        kb_bat_card.setObjectName("card")
        kb_bat_layout = QVBoxLayout(kb_bat_card)
        kb_bat_layout.setContentsMargins(16, 12, 16, 14)
        kb_bat_layout.setSpacing(8)

        kb_bat_top = QHBoxLayout()
        kb_bat_lbl = QLabel("Bateria")
        kb_bat_lbl.setObjectName("sectionTitle")
        self.kb_charging_lbl = QLabel("")
        self.kb_charging_lbl.setObjectName("subtitle")
        self.kb_history_btn = QPushButton("Histórico")
        self.kb_history_btn.setStyleSheet("padding: 3px 9px; font-size: 11px;")
        self.kb_history_btn.setToolTip("Ver gráfico e registo de bateria do teclado")
        self.kb_history_btn.clicked.connect(self._open_kb_battery_history)
        kb_bat_top.addWidget(kb_bat_lbl)
        kb_bat_top.addStretch()
        kb_bat_top.addWidget(self.kb_charging_lbl)
        kb_bat_top.addWidget(self.kb_history_btn)
        kb_bat_layout.addLayout(kb_bat_top)

        kb_bat_row = QHBoxLayout()
        kb_bat_row.setSpacing(10)
        self.kb_battery_bar = QProgressBar()
        self.kb_battery_bar.setRange(0, 100)
        self.kb_battery_bar.setValue(0)
        self.kb_battery_bar.setTextVisible(False)
        self.kb_battery_bar.setFixedHeight(10)
        self.kb_battery_bar.setStyleSheet(
            "QProgressBar { background-color: #252933; border: 1px solid #343a47; border-radius: 5px; }"
            "QProgressBar::chunk { background-color: #4cd964; border-radius: 4px; }"
        )
        self.kb_battery_pct_lbl = QLabel("--%")
        self.kb_battery_pct_lbl.setObjectName("subtitle")
        self.kb_battery_pct_lbl.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #f0f3f8; min-width: 46px;"
        )
        self.kb_battery_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        kb_bat_row.addWidget(self.kb_battery_bar, 1)
        kb_bat_row.addWidget(self.kb_battery_pct_lbl)
        kb_bat_layout.addLayout(kb_bat_row)

        self.kb_battery_note_lbl = QLabel("")
        self.kb_battery_note_lbl.setObjectName("subtitle")
        kb_bat_layout.addWidget(self.kb_battery_note_lbl)
        main_layout.addWidget(kb_bat_card)

        # Card: Perfis rápidos
        self.profiles_card = QFrame()
        self.profiles_card.setObjectName("card")
        prof_layout = QVBoxLayout(self.profiles_card)
        prof_layout.setContentsMargins(16, 12, 16, 14)
        prof_layout.setSpacing(10)

        prof_header = QLabel("Perfis Rápidos")
        prof_header.setObjectName("sectionTitle")
        prof_layout.addWidget(prof_header)

        prof_row = QHBoxLayout()
        prof_row.setSpacing(8)
        self.profile_combo = QComboBox()
        self._refresh_profile_combo()
        self.apply_profile_btn = QPushButton("Aplicar")
        self.apply_profile_btn.clicked.connect(self._apply_selected_profile)
        self.save_profile_btn = QPushButton("Guardar...")
        self.save_profile_btn.setToolTip("Guardar configuração atual (Teclado + Rato) como perfil")
        self.save_profile_btn.clicked.connect(self._save_current_profile)
        self.del_profile_btn = QPushButton("Apagar")
        self.del_profile_btn.clicked.connect(self._delete_selected_profile)

        prof_row.addWidget(self.profile_combo, 1)
        prof_row.addWidget(self.apply_profile_btn)
        prof_row.addWidget(self.save_profile_btn)
        prof_row.addWidget(self.del_profile_btn)
        prof_layout.addLayout(prof_row)
        main_layout.addWidget(self.profiles_card)

        # Card: Automação Silenciosa (partilhada)
        self.auto_card = QFrame()
        self.auto_card.setObjectName("card")
        auto_layout = QVBoxLayout(self.auto_card)
        auto_layout.setContentsMargins(16, 12, 16, 14)
        auto_layout.setSpacing(10)

        auto_top = QHBoxLayout()
        auto_header = QLabel("Automação Silenciosa (Maia)")
        auto_header.setObjectName("sectionTitle")
        self.auto_toggle_btn = QPushButton("Automação: ON")
        self.auto_toggle_btn.setObjectName("powerBtn")
        self.auto_toggle_btn.setCheckable(True)
        self.auto_toggle_btn.clicked.connect(self._on_auto_toggled)
        auto_top.addWidget(auto_header)
        auto_top.addStretch()
        auto_top.addWidget(self.auto_toggle_btn)
        auto_layout.addLayout(auto_top)

        auto_row1 = QHBoxLayout()
        auto_row1.setSpacing(8)
        mode_lbl = QLabel("Ativar")
        mode_lbl.setFixedWidth(85)
        self.auto_mode_combo = QComboBox()
        self.auto_mode_combo.addItem("Pôr do sol (Maia)", "sunset")
        self.auto_mode_combo.addItem("Horário fixo", "fixed")
        self.auto_mode_combo.currentIndexChanged.connect(self._on_auto_settings_changed)

        self.auto_start_edit = QLineEdit("18:00")
        self.auto_start_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.auto_start_edit.editingFinished.connect(self._on_auto_settings_changed)
        self.auto_dash_lbl = QLabel("–")
        self.auto_end_edit = QLineEdit("07:30")
        self.auto_end_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.auto_end_edit.editingFinished.connect(self._on_auto_settings_changed)

        auto_row1.addWidget(mode_lbl)
        auto_row1.addWidget(self.auto_mode_combo, 1)
        auto_row1.addWidget(self.auto_start_edit)
        auto_row1.addWidget(self.auto_dash_lbl)
        auto_row1.addWidget(self.auto_end_edit)
        auto_layout.addLayout(auto_row1)

        auto_row2 = QHBoxLayout()
        auto_row2.setSpacing(8)
        np_lbl = QLabel("Perfil noite")
        np_lbl.setFixedWidth(85)
        self.auto_profile_combo = QComboBox()
        self.auto_profile_combo.currentIndexChanged.connect(self._on_auto_settings_changed)
        auto_row2.addWidget(np_lbl)
        auto_row2.addWidget(self.auto_profile_combo, 1)
        auto_layout.addLayout(auto_row2)

        checks_row = QHBoxLayout()
        self.auto_day_off_chk = QCheckBox("Desligar de dia")
        self.auto_day_off_chk.stateChanged.connect(self._on_auto_settings_changed)
        self.auto_lock_off_chk = QCheckBox("Desligar ao bloquear")
        self.auto_lock_off_chk.stateChanged.connect(self._on_auto_settings_changed)
        self.auto_keyboard_chk = QCheckBox("Teclado")
        self.auto_keyboard_chk.stateChanged.connect(self._on_auto_settings_changed)
        self.auto_mouse_chk = QCheckBox("Rato")
        self.auto_mouse_chk.stateChanged.connect(self._on_auto_settings_changed)
        checks_row.addWidget(self.auto_day_off_chk)
        checks_row.addWidget(self.auto_lock_off_chk)
        checks_row.addWidget(self.auto_keyboard_chk)
        checks_row.addWidget(self.auto_mouse_chk)
        checks_row.addStretch()
        auto_layout.addLayout(checks_row)

        self.auto_info_lbl = QLabel("")
        self.auto_info_lbl.setObjectName("subtitle")
        auto_layout.addWidget(self.auto_info_lbl)

        extra_checks_row = QHBoxLayout()
        self.auto_battery_saver_chk = QCheckBox("Poupança RGB automática (≤20%)")
        self.auto_battery_saver_chk.setToolTip(
            "Reduz automaticamente o brilho RGB para 15% quando a bateria em 2.4G desce abaixo de 20%."
        )
        self.auto_battery_saver_chk.stateChanged.connect(self._on_auto_settings_changed)
        self.autostart_chk = QCheckBox("Iniciar minimizada")
        self.autostart_chk.setChecked(self._is_autostart_enabled())
        self.autostart_chk.stateChanged.connect(self._on_autostart_toggled)
        extra_checks_row.addWidget(self.auto_battery_saver_chk)
        extra_checks_row.addStretch()
        extra_checks_row.addWidget(self.autostart_chk)
        auto_layout.addLayout(extra_checks_row)

        # Card: Efeito & Brilho & Velocidade
        fx_card = QFrame()
        fx_card.setObjectName("card")
        fx_layout = QVBoxLayout(fx_card)
        fx_layout.setContentsMargins(16, 14, 16, 16)
        fx_layout.setSpacing(12)

        fx_header = QLabel("Iluminação")
        fx_header.setObjectName("sectionTitle")
        fx_layout.addWidget(fx_header)

        # Efeito
        eff_row = QHBoxLayout()
        eff_lbl = QLabel("Efeito")
        eff_lbl.setFixedWidth(85)
        self.effect_combo = QComboBox()
        for eff_id, eff_name in EFFECTS:
            self.effect_combo.addItem(eff_name, eff_id)
        self.effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        eff_row.addWidget(eff_lbl)
        eff_row.addWidget(self.effect_combo, 1)
        fx_layout.addLayout(eff_row)

        # Brilho
        bri_row = QHBoxLayout()
        bri_lbl = QLabel("Brilho")
        bri_lbl.setFixedWidth(85)
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 255)
        self.brightness_slider.valueChanged.connect(self._on_brightness_changed)
        self.brightness_val = self._make_value_spinbox(0, 100, "%")
        self.brightness_val.valueChanged.connect(self._on_brightness_spin_changed)
        bri_row.addWidget(bri_lbl)
        bri_row.addWidget(self.brightness_slider, 1)
        bri_row.addWidget(self.brightness_val)
        fx_layout.addLayout(bri_row)

        # Velocidade
        spd_row = QHBoxLayout()
        self.speed_lbl = QLabel("Velocidade")
        self.speed_lbl.setFixedWidth(85)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(0, 255)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        self.speed_val = self._make_value_spinbox(0, 100, "%")
        self.speed_val.valueChanged.connect(self._on_speed_spin_changed)
        spd_row.addWidget(self.speed_lbl)
        spd_row.addWidget(self.speed_slider, 1)
        spd_row.addWidget(self.speed_val)
        fx_layout.addLayout(spd_row)

        main_layout.addWidget(fx_card)

        # Card: Cor
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

        # Amostra + Botão seletor
        picker_row = QHBoxLayout()
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(36, 36)
        self.color_swatch.setStyleSheet(
            "background-color: #ff6b00; border-radius: 8px; border: 1px solid #3e4554;"
        )
        self.picker_btn = QPushButton("Escolher Cor...")
        self.picker_btn.clicked.connect(self._open_color_dialog)
        self.sync_to_mouse_btn = QPushButton("Copiar → Rato")
        self.sync_to_mouse_btn.setToolTip("Aplicar esta cor no rato Glorious Model I 2")
        self.sync_to_mouse_btn.clicked.connect(self._sync_color_to_mouse)
        picker_row.addWidget(self.color_swatch)
        picker_row.addWidget(self.picker_btn)
        picker_row.addWidget(self.sync_to_mouse_btn)
        picker_row.addStretch()
        color_layout.addLayout(picker_row)

        # Hue
        hue_row = QHBoxLayout()
        hue_lbl = QLabel("Tonalidade")
        hue_lbl.setFixedWidth(85)
        self.hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.hue_slider.setObjectName("hueSlider")
        self.hue_slider.setRange(0, 255)
        self.hue_slider.valueChanged.connect(self._on_color_slider_changed)
        self.hue_val = self._make_value_spinbox(0, 255)
        self.hue_val.valueChanged.connect(self._on_color_spin_changed)
        hue_row.addWidget(hue_lbl)
        hue_row.addWidget(self.hue_slider, 1)
        hue_row.addWidget(self.hue_val)
        color_layout.addLayout(hue_row)

        # Saturação
        sat_row = QHBoxLayout()
        sat_lbl = QLabel("Saturação")
        sat_lbl.setFixedWidth(85)
        self.sat_slider = QSlider(Qt.Orientation.Horizontal)
        self.sat_slider.setRange(0, 255)
        self.sat_slider.valueChanged.connect(self._on_color_slider_changed)
        self.sat_val = self._make_value_spinbox(0, 255)
        self.sat_val.valueChanged.connect(self._on_color_spin_changed)
        sat_row.addWidget(sat_lbl)
        sat_row.addWidget(self.sat_slider, 1)
        sat_row.addWidget(self.sat_val)
        color_layout.addLayout(sat_row)

        # Predefinições rápidas
        presets_row = QHBoxLayout()
        presets_row.setSpacing(6)
        for name, h, s, hex_color in COLOR_PRESETS:
            btn = QPushButton()
            btn.setToolTip(name)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hex_color}; border-radius: 14px; "
                f"border: 2px solid #2b303b; padding: 0; }}"
                f"QPushButton:hover {{ border-color: #ffffff; }}"
            )
            btn.clicked.connect(lambda _, hh=h, ss=s: self._apply_preset(hh, ss))
            presets_row.addWidget(btn)
        presets_row.addStretch()
        color_layout.addLayout(presets_row)

        main_layout.addWidget(self.color_card)

        # Rodapé: Atualizar + Guardar na EEPROM
        footer = QHBoxLayout()
        self.feedback_lbl = QLabel("")
        self.feedback_lbl.setObjectName("subtitle")
        self.refresh_btn = QPushButton("Ler do Teclado")
        self.refresh_btn.clicked.connect(self.connect_and_sync)
        self.save_btn = QPushButton("Guardar na Memória")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self._save_eeprom)

        footer.addWidget(self.feedback_lbl, 1)
        footer.addWidget(self.refresh_btn)
        footer.addWidget(self.save_btn)
        main_layout.addLayout(footer)

        self.mouse_tab = MouseTab()
        self.mouse_tab.syncColorRequested.connect(self._apply_rgb_from_mouse)
        self.mouse_tab.batteryUpdated.connect(self._update_tray_tooltip)
        self.tabs.addTab(kb_tab, "Teclado · Keychron V6 Max")
        self.tabs.addTab(self.mouse_tab, "Rato · Glorious Model I 2")
        outer_layout.addWidget(self.tabs)
        outer_layout.addWidget(self.auto_card)
        self._load_auto_ui()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.power_btn.setEnabled(enabled)
        self.profiles_card.setEnabled(enabled)
        self.effect_combo.setEnabled(enabled)
        self.brightness_slider.setEnabled(enabled)
        self.brightness_val.setEnabled(enabled)
        self.speed_slider.setEnabled(enabled)
        self.speed_val.setEnabled(enabled)
        self.color_card.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        self.refresh_btn.setEnabled(enabled)

    def _update_effect_dependent_state(self, effect_id: int) -> None:
        speed_active = effect_id > 1
        color_active = effect_uses_color(effect_id)
        self.speed_slider.setEnabled(speed_active)
        self.speed_val.setEnabled(speed_active)
        self.speed_lbl.setEnabled(speed_active)
        self.hue_slider.setEnabled(color_active)
        self.hue_val.setEnabled(color_active)
        self.sat_slider.setEnabled(color_active)
        self.sat_val.setEnabled(color_active)
        self.picker_btn.setEnabled(color_active)
        self.color_note_lbl.setText(
            "" if color_active else "(não aplicável neste efeito)"
        )

    def _update_swatch(self) -> None:
        c = qmk_hs_to_qcolor(self.hue_slider.value(), self.sat_slider.value())
        self.color_swatch.setStyleSheet(
            f"background-color: {c.name()}; border-radius: 8px; border: 1px solid #3e4554;"
        )

    def connect_and_sync(self) -> None:
        self.feedback_lbl.setText("")
        try:
            path = self.device.connect()
            state = self.device.get_state()
        except Exception as exc:
            self._set_controls_enabled(False)
            self.status_lbl.setText(
                f"Desligado — verifica o cabo USB-C e o interruptor em 'Cable' ({exc})"
            )
            self.status_lbl.setStyleSheet("color: #ff6b6b; font-size: 12px;")
            return

        self.status_lbl.setText(
            f"Ligado via {self.device.connection_mode} ({path}) · VIA v{state.protocol_version}"
        )
        self.status_lbl.setStyleSheet("color: #4cd964; font-size: 12px;")
        self._refresh_kb_charging_label()
        self._set_controls_enabled(True)

        self._syncing = True
        try:
            idx = self.effect_combo.findData(state.effect)
            if idx >= 0:
                self.effect_combo.setCurrentIndex(idx)
            self.brightness_slider.setValue(state.brightness)
            self.brightness_val.setValue(round(state.brightness * 100 / 255))
            self.speed_slider.setValue(state.speed)
            self.speed_val.setValue(round(state.speed * 100 / 255))
            self.hue_slider.setValue(state.hue)
            self.hue_val.setValue(state.hue)
            self.sat_slider.setValue(state.saturation)
            self.sat_val.setValue(state.saturation)
            if state.effect > 0:
                self._last_active_effect = state.effect
            self._update_power_button(state.effect > 0 and state.brightness > 0)
            self._update_swatch()
            self._update_effect_dependent_state(state.effect)
        finally:
            self._syncing = False
        self._refresh_kb_battery()

    def _periodic_kb_check(self) -> None:
        if not self.device.is_connected:
            self.connect_and_sync()
        else:
            self._refresh_kb_battery()

    @staticmethod
    def _load_kb_battery() -> int | None:
        if os.path.exists(KB_STATE_PATH):
            try:
                with open(KB_STATE_PATH, "r", encoding="utf-8") as f:
                    pct = json.load(f).get("battery_pct")
                if isinstance(pct, int) and 0 <= pct <= 100:
                    return pct
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        return None

    @staticmethod
    def _save_kb_battery(pct: int) -> None:
        try:
            with open(KB_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({"battery_pct": pct}, f, indent=2)
        except OSError:
            pass

    def _refresh_kb_charging_label(self) -> None:
        if self.device.connection_mode == "USB-C":
            self.kb_charging_lbl.setText("⚡ Ligado por USB-C")
            self.kb_charging_lbl.setStyleSheet("color: #ffd60a; font-size: 12px;")
        elif self._kb_saver_active:
            self.kb_charging_lbl.setText("Sem fios (2.4G) · Poupança RGB")
            self.kb_charging_lbl.setStyleSheet("color: #ffd60a; font-size: 12px;")
        else:
            self.kb_charging_lbl.setText("Sem fios (2.4G)")
            self.kb_charging_lbl.setStyleSheet("color: #9aa3b2; font-size: 12px;")

    def _open_kb_battery_history(self) -> None:
        dlg = BatteryHistoryDialog(
            "keyboard",
            "Keychron V6 Max",
            current_pct=self._kb_battery_pct,
            is_charging=self.device.connection_mode == "USB-C",
            parent=self,
        )
        dlg.exec()
        self._update_kb_battery_note()

    def _update_kb_battery_note(self) -> None:
        is_charging = self.device.connection_mode == "USB-C"
        summary = format_battery_summary("keyboard", self._kb_battery_pct, is_charging)
        self.kb_battery_note_lbl.setText(summary)
        self.kb_battery_note_lbl.setVisible(bool(summary))

    def _check_kb_battery_saver(self) -> None:
        cfg = load_auto_config()
        enabled = bool(cfg.get("battery_saver_enabled", False))
        threshold = int(cfg.get("battery_saver_threshold", 20))
        saver_bri_pct = int(cfg.get("battery_saver_brightness_pct", 15))
        pct = self._kb_battery_pct
        should_save = (
            enabled
            and self.device.is_connected
            and self.device.connection_mode != "USB-C"
            and pct is not None
            and (pct <= threshold or (self._kb_saver_active and pct < threshold + 5))
        )
        if should_save and not self._kb_saver_active:
            self._kb_saver_active = True
            cur_bri = self.brightness_slider.value()
            self._kb_pre_saver_brightness = cur_bri
            target_raw = min(cur_bri, round(saver_bri_pct * 255 / 100))
            if target_raw < cur_bri:
                try:
                    self.device.set_brightness(target_raw)
                except Exception:
                    pass
            self._refresh_kb_charging_label()
        elif not should_save and self._kb_saver_active:
            self._kb_saver_active = False
            restore_raw = self._kb_pre_saver_brightness
            self._kb_pre_saver_brightness = None
            if restore_raw is not None and self.device.is_connected:
                try:
                    self.device.set_brightness(restore_raw)
                except Exception:
                    pass
            self._refresh_kb_charging_label()

    def _render_kb_battery(self, pct: int) -> None:
        self.kb_battery_bar.setValue(pct)
        self.kb_battery_pct_lbl.setText(f"{pct}%")
        color = "#4cd964" if pct > 50 else ("#ffd60a" if pct > 20 else "#ff453a")
        self.kb_battery_bar.setStyleSheet(
            f"QProgressBar {{ background-color: #252933; border: 1px solid #343a47; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}"
        )
        self._update_kb_battery_note()

    def _refresh_kb_battery(self) -> None:
        if not self.device.is_connected:
            return
        raw_pct = self.device.get_battery()
        if raw_pct is None:
            # Mantém a última percentagem conhecida se o teclado estiver em repouso
            return
        is_charging = self.device.connection_mode == "USB-C"
        if self.device.connection_mode != self._kb_last_conn_mode:
            self._kb_last_conn_mode = self.device.connection_mode
            self._kb_bat_window.clear()

        self._kb_bat_window.append(raw_pct)
        med = sorted(self._kb_bat_window)[len(self._kb_bat_window) // 2]

        # Em 2.4G a bateria não recarrega sozinha: ignora pequenas oscilações ascendentes do ADC
        if not is_charging and self._kb_battery_pct is not None:
            if 0 < (med - self._kb_battery_pct) < 8:
                pct = self._kb_battery_pct
            else:
                pct = med
        else:
            pct = med

        self._kb_battery_pct = pct
        self._save_kb_battery(pct)
        record_battery("keyboard", pct, is_charging)
        self._render_kb_battery(pct)
        self._check_kb_battery_saver()
        self._update_tray_tooltip()
        if is_charging:
            self._kb_low_bat_alerted = False
            if pct == 100 and not self._kb_full_bat_alerted:
                self._kb_full_bat_alerted = True
                notify_full_battery("Keychron V6 Max")
            elif pct < 98:
                self._kb_full_bat_alerted = False
        else:
            self._kb_full_bat_alerted = False
            if pct >= 25:
                self._kb_low_bat_alerted = False
            elif pct <= 20 and not self._kb_low_bat_alerted:
                self._kb_low_bat_alerted = True
                notify_low_battery("Keychron V6 Max", pct)

    def _handle_hid_error(self, exc: Exception) -> None:
        self._set_controls_enabled(False)
        self.status_lbl.setText(f"Ligação perdida: {exc}")
        self.status_lbl.setStyleSheet("color: #ff6b6b; font-size: 12px;")

    def _on_effect_changed(self) -> None:
        if self._syncing or not self.device.is_connected:
            return
        eff_id = self.effect_combo.currentData()
        if eff_id > 0:
            self._last_active_effect = eff_id
        self._update_power_button(eff_id > 0 and self.brightness_slider.value() > 0)
        self._update_effect_dependent_state(eff_id)
        try:
            self.device.set_effect(eff_id)
            self.feedback_lbl.setText("Alterado (não guardado na memória)")
        except Exception as exc:
            self._handle_hid_error(exc)

    def _update_power_button(self, is_on: bool) -> None:
        self.power_btn.blockSignals(True)
        self.power_btn.setChecked(is_on)
        self.power_btn.setText("Luzes: ON" if is_on else "Luzes: OFF")
        self.power_btn.blockSignals(False)

    def _on_power_toggled(self, checked: bool) -> None:
        if not self.device.is_connected:
            return
        self._update_power_button(checked)
        target_effect = self._last_active_effect if checked else 0
        if checked and self.brightness_slider.value() == 0:
            self._syncing = True
            self.brightness_slider.setValue(153)
            self.brightness_val.setValue(60)
            self._syncing = False
            try:
                self.device.set_brightness(153)
            except Exception as exc:
                self._handle_hid_error(exc)
                return

        self._syncing = True
        idx = self.effect_combo.findData(target_effect)
        if idx >= 0:
            self.effect_combo.setCurrentIndex(idx)
        self._update_effect_dependent_state(target_effect)
        self._syncing = False
        try:
            self.device.set_effect(target_effect)
            self.feedback_lbl.setText("Luzes ligadas" if checked else "Luzes desligadas")
        except Exception as exc:
            self._handle_hid_error(exc)

    def _load_profiles(self) -> dict[str, dict[str, int]]:
        if not os.path.exists(PROFILES_PATH):
            self._save_profiles_file(DEFAULT_PROFILES)
            return dict(DEFAULT_PROFILES)
        try:
            with open(PROFILES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return dict(DEFAULT_PROFILES)

    @staticmethod
    def _save_profiles_file(profiles: dict[str, dict[str, int]]) -> None:
        with open(PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)

    def _refresh_profile_combo(self, select_name: str | None = None) -> None:
        self.profile_combo.clear()
        for name in self._profiles:
            self.profile_combo.addItem(name)
        if select_name:
            idx = self.profile_combo.findText(select_name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        if hasattr(self, "auto_profile_combo"):
            prev = self.auto_profile_combo.currentText()
            self.auto_profile_combo.blockSignals(True)
            self.auto_profile_combo.clear()
            for name in self._profiles:
                self.auto_profile_combo.addItem(name)
            idx = self.auto_profile_combo.findText(prev)
            if idx >= 0:
                self.auto_profile_combo.setCurrentIndex(idx)
            self.auto_profile_combo.blockSignals(False)
        self._rebuild_tray_profiles()

    def _load_auto_ui(self) -> None:
        cfg = load_auto_config()
        self._syncing = True
        try:
            active = bool(cfg.get("enabled", True)) and is_service_active()
            self.auto_toggle_btn.setChecked(active)
            self.auto_toggle_btn.setText(
                "Automação: ON" if active else "Automação: OFF"
            )
            mode_idx = self.auto_mode_combo.findData(cfg.get("mode", "sunset"))
            if mode_idx >= 0:
                self.auto_mode_combo.setCurrentIndex(mode_idx)
            self.auto_start_edit.setText(str(cfg.get("start_time", "18:00")))
            self.auto_end_edit.setText(str(cfg.get("end_time", "07:30")))

            self.auto_profile_combo.clear()
            for name in self._profiles:
                self.auto_profile_combo.addItem(name)
            np_idx = self.auto_profile_combo.findText(cfg.get("night_profile", ""))
            if np_idx >= 0:
                self.auto_profile_combo.setCurrentIndex(np_idx)

            self.auto_day_off_chk.setChecked(bool(cfg.get("turn_off_during_day", True)))
            self.auto_lock_off_chk.setChecked(bool(cfg.get("turn_off_on_lock", True)))
            self.auto_keyboard_chk.setChecked(bool(cfg.get("control_keyboard", True)))
            self.auto_mouse_chk.setChecked(bool(cfg.get("control_mouse", True)))
            self.auto_battery_saver_chk.setChecked(bool(cfg.get("battery_saver_enabled", False)))
            self._update_auto_mode_fields()
        finally:
            self._syncing = False

    def _update_auto_mode_fields(self) -> None:
        is_fixed = self.auto_mode_combo.currentData() == "fixed"
        self.auto_start_edit.setVisible(is_fixed)
        self.auto_dash_lbl.setVisible(is_fixed)
        self.auto_end_edit.setVisible(is_fixed)
        if is_fixed:
            self.auto_info_lbl.setText(
                "Serviço silencioso em segundo plano (liga e desliga no horário definido)."
            )
        else:
            sr, ss = get_sun_times()
            self.auto_info_lbl.setText(
                f"Hoje na Maia: nascer às {sr.strftime('%H:%M')} · pôr do sol às {ss.strftime('%H:%M')}"
            )

    def _on_auto_toggled(self, checked: bool) -> None:
        self.auto_toggle_btn.setText("Automação: ON" if checked else "Automação: OFF")
        cfg = load_auto_config()
        cfg["enabled"] = checked
        save_auto_config(cfg)
        ok, err = set_service_enabled(checked)
        if ok:
            self.feedback_lbl.setText(
                "Automação ativada em segundo plano ✓" if checked else "Automação desligada"
            )
        else:
            self.feedback_lbl.setText(f"Erro no serviço: {err}")

    def _on_auto_settings_changed(self) -> None:
        if self._syncing:
            return
        self._update_auto_mode_fields()
        prev_cfg = load_auto_config()
        saver_enabled = self.auto_battery_saver_chk.isChecked()
        cfg = {
            **prev_cfg,
            "enabled": self.auto_toggle_btn.isChecked(),
            "mode": self.auto_mode_combo.currentData(),
            "start_time": self.auto_start_edit.text().strip() or "18:00",
            "end_time": self.auto_end_edit.text().strip() or "07:30",
            "night_profile": self.auto_profile_combo.currentText(),
            "turn_off_during_day": self.auto_day_off_chk.isChecked(),
            "turn_off_on_lock": self.auto_lock_off_chk.isChecked(),
            "control_keyboard": self.auto_keyboard_chk.isChecked(),
            "control_mouse": self.auto_mouse_chk.isChecked(),
            "battery_saver_enabled": saver_enabled,
        }
        save_auto_config(cfg)
        self._check_kb_battery_saver()
        self.mouse_tab.check_battery_saver(
            saver_enabled,
            int(cfg.get("battery_saver_threshold", 20)),
            int(cfg.get("battery_saver_brightness_pct", 15)),
        )
        self.feedback_lbl.setText("Preferências de automação guardadas ✓")

    def _apply_selected_profile(self) -> None:
        name = self.profile_combo.currentText()
        prof = self._profiles.get(name)
        if not prof:
            return

        applied_parts: list[str] = []
        if self.device.is_connected:
            eff = int(prof.get("effect", 1))
            bri = int(prof.get("brightness", 153))
            spd = int(prof.get("speed", 127))
            hue = int(prof.get("hue", 0))
            sat = int(prof.get("saturation", 255))

            try:
                self._apply_and_save_rgb(eff, bri, spd, hue, sat)
            except Exception as exc:
                self._handle_hid_error(exc)
            else:
                self._syncing = True
                idx = self.effect_combo.findData(eff)
                if idx >= 0:
                    self.effect_combo.setCurrentIndex(idx)
                self.brightness_slider.setValue(bri)
                self.brightness_val.setValue(round(bri * 100 / 255))
                self.speed_slider.setValue(spd)
                self.speed_val.setValue(round(spd * 100 / 255))
                self.hue_slider.setValue(hue)
                self.hue_val.setValue(hue)
                self.sat_slider.setValue(sat)
                self.sat_val.setValue(sat)
                if eff > 0:
                    self._last_active_effect = eff
                self._update_power_button(eff > 0 and bri > 0)
                self._update_swatch()
                self._update_effect_dependent_state(eff)
                self._syncing = False
                applied_parts.append("Teclado")

        mprof = prof.get("mouse")
        if isinstance(mprof, dict):
            if self.mouse_tab.apply_profile(mprof):
                applied_parts.append("Rato")

        if applied_parts:
            self.feedback_lbl.setText(
                f"Perfil '{name}' aplicado ({' + '.join(applied_parts)}) ✓"
            )

    def _save_current_profile(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Guardar Perfil", "Nome do novo perfil (Teclado + Rato):"
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        self._profiles[name] = {
            "effect": int(self.effect_combo.currentData()),
            "brightness": self.brightness_slider.value(),
            "speed": self.speed_slider.value(),
            "hue": self.hue_slider.value(),
            "saturation": self.sat_slider.value(),
            "mouse": self.mouse_tab.get_profile_dict(),
        }
        self._save_profiles_file(self._profiles)
        self._refresh_profile_combo(select_name=name)
        self.feedback_lbl.setText(f"Perfil '{name}' (Teclado + Rato) guardado ✓")

    def _delete_selected_profile(self) -> None:
        name = self.profile_combo.currentText()
        if not name or name not in self._profiles:
            return
        del self._profiles[name]
        self._save_profiles_file(self._profiles)
        self._refresh_profile_combo()
        self.feedback_lbl.setText(f"Perfil '{name}' apagado")

    def _on_brightness_changed(self, value: int) -> None:
        if not self._syncing:
            self.brightness_val.blockSignals(True)
            self.brightness_val.setValue(round(value * 100 / 255))
            self.brightness_val.blockSignals(False)
            if self.device.is_connected:
                self._brightness_timer.start()

    def _on_brightness_spin_changed(self, pct: int) -> None:
        if self._syncing:
            return
        raw = round(pct * 255 / 100)
        self.brightness_slider.blockSignals(True)
        self.brightness_slider.setValue(raw)
        self.brightness_slider.blockSignals(False)
        if self.device.is_connected:
            self._flush_brightness()

    def _flush_brightness(self) -> None:
        try:
            self.device.set_brightness(self.brightness_slider.value())
            self.feedback_lbl.setText("Alterado (não guardado na memória)")
        except Exception as exc:
            self._handle_hid_error(exc)

    def _on_speed_changed(self, value: int) -> None:
        if not self._syncing:
            self.speed_val.blockSignals(True)
            self.speed_val.setValue(round(value * 100 / 255))
            self.speed_val.blockSignals(False)
            if self.device.is_connected:
                self._speed_timer.start()

    def _on_speed_spin_changed(self, pct: int) -> None:
        if self._syncing:
            return
        raw = round(pct * 255 / 100)
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(raw)
        self.speed_slider.blockSignals(False)
        if self.device.is_connected:
            self._flush_speed()

    def _flush_speed(self) -> None:
        try:
            self.device.set_speed(self.speed_slider.value())
            self.feedback_lbl.setText("Alterado (não guardado na memória)")
        except Exception as exc:
            self._handle_hid_error(exc)

    def _on_color_slider_changed(self) -> None:
        if not self._syncing:
            self.hue_val.blockSignals(True)
            self.sat_val.blockSignals(True)
            self.hue_val.setValue(self.hue_slider.value())
            self.sat_val.setValue(self.sat_slider.value())
            self.hue_val.blockSignals(False)
            self.sat_val.blockSignals(False)
        self._update_swatch()
        if not self._syncing and self.device.is_connected:
            self._color_timer.start()

    def _on_color_spin_changed(self) -> None:
        if self._syncing:
            return
        self.hue_slider.blockSignals(True)
        self.sat_slider.blockSignals(True)
        self.hue_slider.setValue(self.hue_val.value())
        self.sat_slider.setValue(self.sat_val.value())
        self.hue_slider.blockSignals(False)
        self.sat_slider.blockSignals(False)
        self._update_swatch()
        if self.device.is_connected:
            self._flush_color()

    def _flush_color(self) -> None:
        try:
            self.device.set_color(self.hue_slider.value(), self.sat_slider.value())
            self.feedback_lbl.setText("Alterado (não guardado na memória)")
        except Exception as exc:
            self._handle_hid_error(exc)

    def _apply_preset(self, hue: int, sat: int) -> None:
        if not self.device.is_connected:
            return
        self._syncing = True
        self.hue_slider.setValue(hue)
        self.sat_slider.setValue(sat)
        self.hue_val.setValue(hue)
        self.sat_val.setValue(sat)
        self._update_swatch()
        self._syncing = False
        self._flush_color()

    def _open_color_dialog(self) -> None:
        initial = qmk_hs_to_qcolor(self.hue_slider.value(), self.sat_slider.value())
        chosen = QColorDialog.getColor(initial, self, "Escolher Cor RGB")
        if chosen.isValid():
            h, s = qcolor_to_qmk_hs(chosen)
            self._apply_preset(h, s)

    def _apply_and_save_rgb(self, eff: int, bri: int, spd: int, hue: int, sat: int) -> None:
        # O QMK via_qmk_rgb_matrix_save() usa '==' na máscara de bits, pelo que só grava
        # se cada campo for guardado individualmente após ser alterado.
        self.device.set_effect(eff)
        self.device.save_eeprom()
        self.device.set_brightness(bri)
        self.device.save_eeprom()
        self.device.set_speed(spd)
        self.device.save_eeprom()
        self.device.set_color(hue, sat)
        self.device.save_eeprom()

    def _save_eeprom(self) -> None:
        try:
            self._apply_and_save_rgb(
                int(self.effect_combo.currentData()),
                self.brightness_slider.value(),
                self.speed_slider.value(),
                self.hue_slider.value(),
                self.sat_slider.value(),
            )
            self.feedback_lbl.setText("Guardado na memória do teclado ✓")
        except Exception as exc:
            self._handle_hid_error(exc)

    def _setup_tray(self) -> None:
        self._force_quit = False
        self._tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")
        tray = QSystemTrayIcon(QIcon(icon_path), self)
        menu = QMenu()

        self._tray_bat_action = menu.addAction("Rato: --%")
        self._tray_bat_action.setEnabled(False)
        menu.addSeparator()

        open_act = menu.addAction("Abrir Periféricos RGB")
        open_act.triggered.connect(self._show_window)

        kb_toggle_act = menu.addAction("Teclado: Ligar / Desligar Luzes")
        kb_toggle_act.triggered.connect(lambda: self.power_btn.click())

        mouse_toggle_act = menu.addAction("Rato: Ligar / Desligar Luzes")
        mouse_toggle_act.triggered.connect(lambda: self.mouse_tab.power_btn.click())

        self._tray_profiles_menu = menu.addMenu("Perfis (Teclado + Rato)")
        self._rebuild_tray_profiles()

        menu.addSeparator()
        quit_act = menu.addAction("Sair")
        quit_act.triggered.connect(self._quit_app)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray

        self._tray_timer = QTimer(self)
        self._tray_timer.setInterval(3000)
        self._tray_timer.timeout.connect(self._update_tray_tooltip)
        self._tray_timer.start()
        self._update_tray_tooltip()

    def _rebuild_tray_profiles(self) -> None:
        if not hasattr(self, "_tray_profiles_menu"):
            return
        self._tray_profiles_menu.clear()
        for name in self._profiles:
            act = QAction(name, self._tray_profiles_menu)
            act.triggered.connect(lambda _, n=name: self._apply_profile_by_name(n))
            self._tray_profiles_menu.addAction(act)

    def _apply_profile_by_name(self, name: str) -> None:
        idx = self.profile_combo.findText(name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
            self._apply_selected_profile()

    def _build_tray_icon(
        self,
        kb_pct: int | None,
        mouse_pct: int | None,
        any_charging: bool,
    ) -> QIcon:
        vals = [v for v in (kb_pct, mouse_pct) if v is not None]
        min_pct = min(vals) if vals else None
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")
        base_icon = QIcon(icon_path)
        pm = base_icon.pixmap(64, 64)
        if pm.isNull():
            pm = QPixmap(64, 64)
            pm.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if min_pct is None:
            badge_color = QColor("#7c8598")
            text = "--"
        else:
            if min_pct > 50:
                badge_color = QColor("#4cd964")
            elif min_pct > 20:
                badge_color = QColor("#ffd60a")
            else:
                badge_color = QColor("#ff453a")
            text = f"{min_pct}"

        badge_rect = QRect(22, 30, 40, 32)
        painter.setPen(QPen(QColor("#121418"), 3))
        painter.setBrush(QBrush(badge_color))
        painter.drawRoundedRect(badge_rect, 10, 10)

        font = QFont("Inter", 14, QFont.Weight.Bold)
        font.setPixelSize(18 if len(text) <= 2 else 14)
        painter.setFont(font)
        text_color = QColor("#121418") if min_pct is not None and min_pct > 20 else QColor("#ffffff")
        painter.setPen(text_color)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)

        if any_charging:
            dot_rect = QRect(4, 4, 14, 14)
            painter.setPen(QPen(QColor("#121418"), 2))
            painter.setBrush(QBrush(QColor("#ffd60a")))
            painter.drawEllipse(dot_rect)

        painter.end()
        return QIcon(pm)

    def _update_tray_tooltip(self) -> None:
        if not self._tray:
            return
        pct = self.mouse_tab.state.battery_pct
        mouse_charging = self.mouse_tab.device.conn_type == "usb"
        kb_charging = self.device.connection_mode == "USB-C"
        bat_str = f"{pct}%" if pct is not None else "--%"
        if mouse_charging:
            bat_str += " (A carregar)"
        kb_pct = self._kb_battery_pct
        kb_str = f"{kb_pct}%" if kb_pct is not None else "--%"
        if kb_charging:
            kb_str += " (USB-C)"
        self._tray_bat_action.setText(f"Teclado: {kb_str}  |  Rato: {bat_str}")
        self._tray.setToolTip(f"Periféricos RGB — Teclado: {kb_str} | Rato: {bat_str}")

        icon_key = (kb_pct, pct, kb_charging or mouse_charging)
        if icon_key != self._last_tray_icon_key:
            self._last_tray_icon_key = icon_key
            self._tray.setIcon(self._build_tray_icon(kb_pct, pct, kb_charging or mouse_charging))

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.hide()
            else:
                self._show_window()

    def _sync_color_to_mouse(self) -> None:
        c = qmk_hs_to_qcolor(self.hue_slider.value(), self.sat_slider.value())
        self.mouse_tab._apply_color((c.red(), c.green(), c.blue()))
        self.feedback_lbl.setText("Cor copiada para o rato ✓")

    def _apply_rgb_from_mouse(self, rgb: tuple[int, int, int]) -> None:
        if not self.device.is_connected:
            self.mouse_tab.feedback_lbl.setText("Teclado desligado para copiar cor")
            return
        h, s = qcolor_to_qmk_hs(QColor(rgb[0], rgb[1], rgb[2]))
        self._apply_preset(h, s)
        self.mouse_tab.feedback_lbl.setText("Cor copiada para o teclado ✓")

    @staticmethod
    def _autostart_path() -> str:
        return os.path.expanduser("~/.config/autostart/keychron-rgb.desktop")

    def _is_autostart_enabled(self) -> bool:
        return os.path.exists(self._autostart_path())

    def _on_autostart_toggled(self, state: int) -> None:
        path = self._autostart_path()
        if state:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            app_dir = os.path.dirname(os.path.abspath(__file__))
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Periféricos RGB\n"
                    f"Exec=python3 {app_dir}/app.py --minimized\n"
                    f"Icon={app_dir}/icon.svg\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
            self.feedback_lbl.setText("Arranque minimizado ativado ✓")
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            self.feedback_lbl.setText("Arranque automático desativado")

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        self._force_quit = True
        self.close()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._tray and self._tray.isVisible() and not self._force_quit:
            event.ignore()
            self.hide()
            return
        if self._tray:
            self._tray.hide()
        self.device.close()
        self.mouse_tab.close_device()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLESHEET)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")
    app.setWindowIcon(QIcon(icon_path))
    win = KeychronRGBWindow()
    if "--minimized" not in sys.argv or win._tray is None:
        win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
