"""Módulo de comunicação Raw HID (protocolo VIA) para o Keychron V6 Max."""

from __future__ import annotations

import glob
import os
import select
import time
from dataclasses import dataclass

from shared_utils import hid_lock

VENDOR_ID = 0x3434
PRODUCT_ID = 0x0961
DONGLE_PRODUCT_ID = 0xD030
VIA_USAGE_PAGE_PREFIX = b"\x06\x60\xff"  # Usage Page (Vendor Defined 0xFF60)

CMD_GET_PROTOCOL_VERSION = 0x01
CMD_CUSTOM_SET_VALUE = 0x07
CMD_CUSTOM_GET_VALUE = 0x08
CMD_CUSTOM_SAVE = 0x09
CMD_GET_BATTERY = 0xAC

RGB_CHANNEL_ID = 3
VAL_BRIGHTNESS = 1
VAL_EFFECT = 2
VAL_EFFECT_SPEED = 3
VAL_COLOR = 4

EFFECTS: list[tuple[int, str]] = [
    (0, "None (Desligado)"),
    (1, "Solid Color (Cor Sólida)"),
    (2, "Breathing"),
    (3, "Band Spiral Val"),
    (4, "Cycle All"),
    (5, "Cycle Left Right"),
    (6, "Cycle Up Down"),
    (7, "Rainbow Moving Chevron"),
    (8, "Cycle Out In"),
    (9, "Cycle Out In Dual"),
    (10, "Cycle Pinwheel"),
    (11, "Cycle Spiral"),
    (12, "Dual Beacon"),
    (13, "Rainbow Beacon"),
    (14, "Jellybean Raindrops"),
    (15, "Pixel Rain"),
    (16, "Typing Heatmap"),
    (17, "Digital Rain"),
    (18, "Reactive Simple"),
    (19, "Reactive Multiwide"),
    (20, "Reactive Multinexus"),
    (21, "Splash"),
    (22, "Solid Splash"),
]


@dataclass
class RGBState:
    protocol_version: int
    brightness: int
    effect: int
    speed: int
    hue: int
    saturation: int


def find_via_hidraw(vid: int = VENDOR_ID, pid: int = PRODUCT_ID) -> str | None:
    """Procura o /dev/hidraw* do teclado com a interface VIA (Usage Page 0xFF60)."""
    target_hid_id = f":{vid:08X}:{pid:08X}"
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
            if VIA_USAGE_PAGE_PREFIX in desc[:8]:
                return f"/dev/{os.path.basename(sys_path)}"
        except OSError:
            continue
    return None


class KeychronRGBDevice:
    """Cliente VIA Raw HID para controlar o RGB Matrix do Keychron V6 Max."""

    def __init__(self) -> None:
        self.fd: int | None = None
        self.path: str | None = None
        self.connection_mode: str = ""

    @property
    def is_connected(self) -> bool:
        return self.fd is not None

    def connect(self) -> str:
        with hid_lock():
            self.close()
            candidates = [
                (PRODUCT_ID, "USB-C"),
                (DONGLE_PRODUCT_ID, "2.4G Keychron Link"),
            ]
            found_any = False
            for pid, mode_label in candidates:
                path = find_via_hidraw(VENDOR_ID, pid)
                if not path:
                    continue
                found_any = True
                try:
                    self.fd = os.open(path, os.O_RDWR)
                    self.path = path
                    self._xfer([CMD_GET_PROTOCOL_VERSION], timeout=1.5)
                    self.connection_mode = mode_label
                    return path
                except Exception:
                    self.close()

            if found_any:
                raise TimeoutError(
                    "Teclado encontrado mas sem resposta (verifica se está acordado em 2.4G ou ligado em Cable)."
                )
            raise FileNotFoundError(
                "Keychron V6 Max / Keychron Link não encontrado."
            )

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
            self.path = None
            self.connection_mode = ""

    def _drain_rx(self) -> None:
        """Descarta pacotes pendentes no buffer hidraw antes de enviar um novo comando."""
        if self.fd is None:
            return
        for _ in range(16):
            ready, _, _ = select.select([self.fd], [], [], 0)
            if not ready:
                break
            try:
                os.read(self.fd, 32)
            except OSError:
                break

    def _xfer(self, payload: list[int], timeout: float = 1.5) -> list[int]:
        with hid_lock():
            if self.fd is None:
                raise ConnectionError("Dispositivo não ligado.")
            pkt = bytes([0x00] + payload + [0] * (32 - len(payload)))
            expected_cmd = payload[0]
            try:
                self._drain_rx()
                os.write(self.fd, pkt)
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Tempo limite excedido ao comunicar com o teclado.")
                    ready, _, _ = select.select([self.fd], [], [], remaining)
                    if not ready:
                        raise TimeoutError("Tempo limite excedido ao comunicar com o teclado.")
                    resp = os.read(self.fd, 32)
                    if resp and resp[0] == expected_cmd:
                        return list(resp)
            except OSError as exc:
                self.close()
                raise ConnectionError(f"Erro de comunicação HID: {exc}") from exc

    def get_state(self) -> RGBState:
        with hid_lock():
            proto = self._xfer([CMD_GET_PROTOCOL_VERSION])
            protocol_version = (proto[1] << 8) | proto[2]
            brightness = self._xfer([CMD_CUSTOM_GET_VALUE, RGB_CHANNEL_ID, VAL_BRIGHTNESS])[3]
            effect = self._xfer([CMD_CUSTOM_GET_VALUE, RGB_CHANNEL_ID, VAL_EFFECT])[3]
            speed = self._xfer([CMD_CUSTOM_GET_VALUE, RGB_CHANNEL_ID, VAL_EFFECT_SPEED])[3]
            color_resp = self._xfer([CMD_CUSTOM_GET_VALUE, RGB_CHANNEL_ID, VAL_COLOR])
            hue = color_resp[3]
            saturation = color_resp[4]
            return RGBState(
                protocol_version=protocol_version,
                brightness=brightness,
                effect=effect,
                speed=speed,
                hue=hue,
                saturation=saturation,
            )

    def set_brightness(self, value: int) -> None:
        val = max(0, min(255, int(value)))
        self._xfer([CMD_CUSTOM_SET_VALUE, RGB_CHANNEL_ID, VAL_BRIGHTNESS, val])

    def set_effect(self, effect_id: int) -> None:
        val = max(0, min(255, int(effect_id)))
        self._xfer([CMD_CUSTOM_SET_VALUE, RGB_CHANNEL_ID, VAL_EFFECT, val])

    def set_speed(self, value: int) -> None:
        val = max(0, min(255, int(value)))
        self._xfer([CMD_CUSTOM_SET_VALUE, RGB_CHANNEL_ID, VAL_EFFECT_SPEED, val])

    def set_color(self, hue: int, saturation: int) -> None:
        h = max(0, min(255, int(hue)))
        s = max(0, min(255, int(saturation)))
        self._xfer([CMD_CUSTOM_SET_VALUE, RGB_CHANNEL_ID, VAL_COLOR, h, s])

    def save_eeprom(self) -> None:
        self._xfer([CMD_CUSTOM_SAVE, RGB_CHANNEL_ID])

    def apply_and_save_rgb(self, eff: int, bri: int, spd: int, hue: int, sat: int) -> None:
        """Aplica todos os parâmetros RGB e grava cada campo na EEPROM (workaround da máscara VIA)."""
        with hid_lock():
            self.set_effect(eff)
            self.save_eeprom()
            self.set_brightness(bri)
            self.save_eeprom()
            self.set_speed(spd)
            self.save_eeprom()
            self.set_color(hue, sat)
            self.save_eeprom()

    def disable_and_save(self) -> None:
        """Desliga o efeito RGB e grava na EEPROM para não reacender após sleep/indicadores."""
        with hid_lock():
            self.set_effect(0)
            self.save_eeprom()

    def get_battery(self) -> int | None:
        """Lê a % de bateria do teclado (requer patch 0xAC no firmware)."""
        for _ in range(2):
            try:
                resp = self._xfer([CMD_GET_BATTERY], timeout=1.5)
                if resp[0] == CMD_GET_BATTERY and 0 <= resp[1] <= 100:
                    return resp[1]
            except Exception:
                pass
        return None
