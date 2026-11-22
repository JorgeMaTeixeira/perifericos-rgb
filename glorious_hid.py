"""Comunicação HID para o rato Glorious Model I 2 Wireless (2.4G e USB-C)."""

from __future__ import annotations

import fcntl
import glob
import json
import os
import select
import time
from dataclasses import dataclass, field

VENDOR_ID = 0x093A
PID_USB = 0x821A
PID_RECEIVER = 0x821D
USAGE_PAGE_PREFIX = b"\x06\x00\xff\x09\x01"

CMD_LIGHTING = 0x02
CMD_PERFORMANCE = 0x04
TARGET_RECEIVER = 0xFB
TARGET_USB = 0xFA

POLLING_RATES: dict[int, int] = {125: 1, 250: 2, 500: 3, 1000: 4}
LIFT_OFF_OPTIONS: list[tuple[int, str]] = [(1, "1.0 mm"), (2, "2.0 mm")]
DPI_MIN, DPI_MAX, DPI_STEP = 100, 26000, 50
DEFAULT_DPI_STAGES: list[tuple[int, tuple[int, int, int]]] = [
    (400, (255, 255, 0)),
    (800, (0, 180, 255)),
    (1600, (255, 40, 40)),
    (3200, (0, 220, 100)),
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MOUSE_STATE_PATH = os.path.join(BASE_DIR, "mouse_state.json")

MOUSE_EFFECTS: list[tuple[int, str]] = [
    (0, "LED Off"),
    (1, "Glorious Mode"),
    (2, "Seamless Breathing"),
    (3, "Breathing"),
    (4, "Single Color"),
    (5, "Breathing Single Color"),
    (6, "Tail"),
    (7, "Rave"),
    (8, "Wave"),
]

# Efeitos que usam cor única/personalizada
MOUSE_COLOR_EFFECTS = {2, 3, 4, 5, 6, 7}


def _hidiocsfeature(length: int) -> int:
    """Calcula o ioctl HIDIOCSFEATURE(length) para Linux."""
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


def _scale_0_100(v: float, zero_is_off: bool) -> int:
    if v > 4:
        return int(v * 20 / 100)
    if zero_is_off and v == 0:
        return 0
    return 1


def find_glorious_hidraw() -> tuple[str, str] | None:
    """Procura o /dev/hidraw* do Model I 2 Wireless. Prefere USB-C se ligado."""
    candidates = [(PID_USB, "usb"), (PID_RECEIVER, "receiver")]
    for pid, conn_type in candidates:
        target_hid_id = f":{VENDOR_ID:08X}:{pid:08X}"
        for sys_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            uevent_path = os.path.join(sys_path, "device", "uevent")
            desc_path = os.path.join(sys_path, "device", "report_descriptor")
            try:
                with open(uevent_path, "r", encoding="utf-8", errors="ignore") as f:
                    uevent = f.read()
                if target_hid_id not in uevent.upper():
                    continue
                with open(desc_path, "rb") as f:
                    desc = f.read(16)
                if desc.startswith(USAGE_PAGE_PREFIX):
                    return f"/dev/{os.path.basename(sys_path)}", conn_type
            except OSError:
                continue
    return None


def find_glorious_mouse_event() -> str | None:
    """Procura o /dev/input/event* do rato Glorious (eixos X/Y) via receiver 2.4G."""
    target_vid = f"{VENDOR_ID:04x}"
    target_pid = f"{PID_RECEIVER:04x}"
    for sys_path in sorted(glob.glob("/sys/class/input/event*")):
        try:
            with open(os.path.join(sys_path, "device", "id", "vendor"), "r", encoding="utf-8") as f:
                vid = f.read().strip().lower()
            with open(os.path.join(sys_path, "device", "id", "product"), "r", encoding="utf-8") as f:
                pid = f.read().strip().lower()
            if vid != target_vid or pid != target_pid:
                continue
            rel_path = os.path.join(sys_path, "device", "capabilities", "rel")
            with open(rel_path, "r", encoding="utf-8") as f:
                rel_caps = int(f.read().strip().split()[-1], 16)
            if (rel_caps & 0x3) != 0x3:
                continue
            ev_path = f"/dev/input/{os.path.basename(sys_path)}"
            if os.access(ev_path, os.R_OK):
                return ev_path
        except (OSError, ValueError, IndexError):
            continue
    return None


@dataclass
class MouseState:
    effect: int = 1
    last_active_effect: int = 4
    brightness: int = 80
    speed: int = 50
    color: tuple[int, int, int] = (255, 110, 0)
    battery_pct: int | None = None
    polling_rate: int = 1000
    lift_off: int = 1
    debounce: int = 2
    active_dpi_stage: int = 1
    dpi_stages: list[tuple[int, tuple[int, int, int]]] = field(
        default_factory=lambda: list(DEFAULT_DPI_STAGES)
    )

    def to_dict(self) -> dict:
        return {
            "effect": self.effect,
            "last_active_effect": self.last_active_effect,
            "brightness": self.brightness,
            "speed": self.speed,
            "color": list(self.color),
            "battery_pct": self.battery_pct,
            "polling_rate": self.polling_rate,
            "lift_off": self.lift_off,
            "debounce": self.debounce,
            "active_dpi_stage": self.active_dpi_stage,
            "dpi_stages": [
                {"dpi": dpi, "color": list(rgb)} for dpi, rgb in self.dpi_stages
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MouseState":
        col = data.get("color", [255, 110, 0])
        raw_stages = data.get("dpi_stages")
        stages: list[tuple[int, tuple[int, int, int]]] = []
        if isinstance(raw_stages, list) and raw_stages:
            for idx, item in enumerate(raw_stages[:7]):
                if isinstance(item, dict):
                    dpi = max(DPI_MIN, min(DPI_MAX, int(item.get("dpi", 800))))
                    c = item.get("color", DEFAULT_DPI_STAGES[idx % len(DEFAULT_DPI_STAGES)][1])
                    stages.append((dpi, (int(c[0]), int(c[1]), int(c[2]))))
        if not stages:
            stages = list(DEFAULT_DPI_STAGES)
        polling = int(data.get("polling_rate", 1000))
        if polling not in POLLING_RATES:
            polling = 1000
        lod = int(data.get("lift_off", 1))
        if lod not in (1, 2):
            lod = 1
        deb = max(0, min(16, int(data.get("debounce", 2))))
        active = max(0, min(len(stages) - 1, int(data.get("active_dpi_stage", 1))))
        eff = int(data.get("effect", 1))
        last_eff = int(data.get("last_active_effect", eff if eff > 0 else 4))
        if last_eff <= 0:
            last_eff = 4
        return cls(
            effect=eff,
            last_active_effect=last_eff,
            brightness=int(data.get("brightness", 80)),
            speed=int(data.get("speed", 50)),
            color=(int(col[0]), int(col[1]), int(col[2])),
            battery_pct=data.get("battery_pct"),
            polling_rate=polling,
            lift_off=lod,
            debounce=deb,
            active_dpi_stage=active,
            dpi_stages=stages,
        )


def load_mouse_state() -> MouseState:
    if os.path.exists(MOUSE_STATE_PATH):
        try:
            with open(MOUSE_STATE_PATH, "r", encoding="utf-8") as f:
                return MouseState.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
    return MouseState()


def save_mouse_state(state: MouseState) -> None:
    try:
        with open(MOUSE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)
    except OSError:
        pass


def build_lighting_reports(
    effect_id: int,
    brightness_pct: int,
    speed_pct: int,
    rgb: tuple[int, int, int],
    conn: str,
    profile: int = 0,
) -> list[bytes]:
    """Constrói os Feature Reports 0x03 (64 bytes) para o Model I 2 Wireless."""
    data = bytearray(256)
    rate = _scale_0_100(max(0, min(100, speed_pct)), zero_is_off=False)
    bright = _scale_0_100(max(0, min(100, brightness_pct)), zero_is_off=True)
    data[0] = rate
    data[1] = bright
    data[2] = 1  # 1 cor
    data[3] = rate
    data[4] = bright

    color_off = 11 if conn == "usb" else 5
    r, g, b = (max(0, min(255, c)) for c in rgb)
    data[color_off : color_off + 3] = bytes([r, g, b])

    target = TARGET_USB if conn == "usb" else TARGET_RECEIVER
    eid = max(0, min(8, int(effect_id)))

    if conn == "usb":
        buf = bytearray(64)
        buf[0:5] = bytes([0x03, CMD_LIGHTING, target, profile + 1, eid])
        buf[5:34] = data[0:29]
        return [bytes(buf)]

    b1 = bytearray(64)
    b1[0:6] = bytes([0x03, CMD_LIGHTING, target, 0, profile + 1, eid])
    b1[6:14] = data[0:8]
    b2 = bytearray(b1)
    b2[3] = 1
    b2[6:15] = data[8:17]
    b3 = bytearray(b2)
    b3[3] = 2
    b3[6:15] = data[17:26]
    return [bytes(b1), bytes(b2), bytes(b3)]


def build_performance_reports(
    dpi_stages: list[tuple[int, tuple[int, int, int]]],
    active_stage: int,
    polling_rate: int,
    conn: str,
    lift_off: int = 1,
    debounce: int = 2,
    profile: int = 0,
) -> list[bytes]:
    """Constrói os Feature Reports 0x03 (CMD_PERFORMANCE 0x04) para o Model I 2 Wireless."""
    if not 1 <= len(dpi_stages) <= 7:
        raise ValueError("Número de níveis DPI deve estar entre 1 e 7.")
    if polling_rate not in POLLING_RATES:
        raise ValueError(f"Polling rate inválido: {polling_rate}")
    active = max(0, min(len(dpi_stages) - 1, int(active_stage)))
    lod = 2 if int(lift_off) == 2 else 1
    deb = max(0, min(16, int(debounce)))

    data = bytearray(256)
    data[0] = active
    data[1] = len(dpi_stages)
    data[2] = lod
    data[3] = deb
    data[4] = POLLING_RATES[polling_rate]
    data[5] = 0  # motion_sync

    first, stride = (12, 8) if conn == "usb" else (6, 5)
    for i, (dpi, rgb) in enumerate(dpi_stages):
        clamped_dpi = max(DPI_MIN, min(DPI_MAX, int(dpi)))
        hw = round(clamped_dpi / DPI_STEP)
        r, g, b = (max(0, min(255, int(c))) for c in rgb)
        off = first + stride * i
        data[off : off + 5] = bytes([hw & 0xFF, (hw >> 8) & 0xFF, r, g, b])

    target = TARGET_USB if conn == "usb" else TARGET_RECEIVER
    if conn == "usb":
        buf = bytearray(64)
        buf[0:4] = bytes([0x03, CMD_PERFORMANCE, target, profile + 1])
        buf[4:64] = data[0:60]
        return [bytes(buf)]

    out: list[bytes] = []
    start, end = 0, 11
    for chunk in range(4):
        buf = bytearray(64)
        buf[0:5] = bytes([0x03, CMD_PERFORMANCE, target, chunk, profile + 1])
        piece = data[start:end]
        buf[5 : 5 + len(piece)] = piece
        out.append(bytes(buf))
        start, end = end, end + 10
    return out


class GloriousMouseDevice:
    """Cliente HID para o Glorious Model I 2 Wireless (2.4G ou USB-C)."""

    def __init__(self) -> None:
        self.fd: int | None = None
        self.path: str | None = None
        self.conn_type: str = ""  # "usb" ou "receiver"

    @property
    def is_connected(self) -> bool:
        return self.fd is not None

    @property
    def connection_label(self) -> str:
        if self.conn_type == "usb":
            return "USB-C (A carregar)"
        if self.conn_type == "receiver":
            return "2.4G Wireless"
        return "Desligado"

    def connect(self) -> str:
        self.close()
        found = find_glorious_hidraw()
        if not found:
            raise FileNotFoundError(
                "Glorious Model I 2 Wireless não encontrado (nem dongle nem cabo)."
            )
        path, conn_type = found
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.path = path
        self.conn_type = conn_type
        return path

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
            self.path = None
            self.conn_type = ""

    def set_lighting(
        self,
        effect_id: int,
        brightness_pct: int,
        speed_pct: int,
        rgb: tuple[int, int, int],
    ) -> None:
        if self.fd is None:
            raise ConnectionError("Rato não ligado.")
        reports = build_lighting_reports(
            effect_id=effect_id,
            brightness_pct=brightness_pct,
            speed_pct=speed_pct,
            rgb=rgb,
            conn=self.conn_type,
        )
        pacing = 0.10 if self.conn_type == "usb" else 0.05
        req = _hidiocsfeature(64)
        try:
            for idx, rpt in enumerate(reports):
                fcntl.ioctl(self.fd, req, bytearray(rpt))
                if idx < len(reports) - 1:
                    time.sleep(pacing)
        except OSError as exc:
            self.close()
            raise ConnectionError(f"Erro HID ao configurar rato: {exc}") from exc

    def set_performance(
        self,
        dpi_stages: list[tuple[int, tuple[int, int, int]]],
        active_stage: int,
        polling_rate: int,
        lift_off: int = 1,
        debounce: int = 2,
    ) -> None:
        if self.fd is None:
            raise ConnectionError("Rato não ligado.")
        reports = build_performance_reports(
            dpi_stages=dpi_stages,
            active_stage=active_stage,
            polling_rate=polling_rate,
            conn=self.conn_type,
            lift_off=lift_off,
            debounce=debounce,
        )
        req = _hidiocsfeature(64)
        try:
            for idx, rpt in enumerate(reports):
                fcntl.ioctl(self.fd, req, bytearray(rpt))
                if idx < len(reports) - 1:
                    time.sleep(0.15)
        except OSError as exc:
            self.close()
            raise ConnectionError(f"Erro HID ao configurar desempenho do rato: {exc}") from exc

    def poll_battery_frame(self) -> int | None:
        """Lê frames pendentes sem bloquear e devolve a % de bateria se recebida."""
        if self.fd is None:
            return None
        latest_pct: int | None = None
        try:
            while True:
                ready, _, _ = select.select([self.fd], [], [], 0.0)
                if not ready:
                    break
                data = os.read(self.fd, 64)
                if len(data) >= 3 and data[0] == 0x06 and data[1] == 0xFB:
                    pct = data[2]
                    if 0 <= pct <= 100:
                        latest_pct = pct
        except BlockingIOError:
            pass
        except OSError:
            self.close()
        return latest_pct
