"""Comunicação Bluetooth RFCOMM (protocolo Xiaomi/JieLi RCSP) para REDMI Headphones Neo."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import re
import socket
import subprocess
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HEADPHONES_STATE_PATH = os.path.join(BASE_DIR, "headphones_state.json")

TARGET_NAME = "REDMI Headphones Neo"
RFCOMM_CHANNEL = 6

# Modos ANC (opcode 0x08 / attr 0x000B)
ANC_MODES: dict[int, str] = {
    0: "Desligado",
    1: "Cancelamento de Ruído",
    2: "Transparência",
}

# Presets de Equalizador observados na captura (attr 0x0007)
EQ_PRESETS: dict[int, str] = {
    0x15: "Som Balanceado",
    0x05: "Melhorar Graves",
    0x06: "Melhorar Agudos",
    0x01: "Melhorar Voz",
    0x0A: "Personalizado",
}

# Perfis rápidos (nome -> (anc_mode, eq_preset))
QUICK_PROFILES: dict[str, tuple[int, int]] = {
    "Foco (ANC + Balanceado)": (1, 0x15),
    "Chamada (Transp. + Voz)": (2, 0x01),
    "Música (Graves)": (0, 0x05),
}

AUDIO_PROFILE_LABELS: dict[str, str] = {
    "a2dp-sink-sbc_xq": "Alta Fidelidade (A2DP SBC-XQ)",
    "a2dp-sink": "Alta Fidelidade (A2DP SBC)",
    "a2dp-sink-aac": "Alta Fidelidade (A2DP AAC)",
    "headset-head-unit-msbc": "Chamada / Microfone (HFP mSBC)",
    "headset-head-unit-cvsd": "Chamada / Microfone (HFP CVSD)",
    "headset-head-unit": "Chamada / Microfone (HSP/HFP)",
}


@dataclass
class HeadphonesState:
    mac: str = ""
    name: str = TARGET_NAME
    battery_pct: int | None = None
    anc_mode: int = 0
    eq_preset: int = 0x15
    dual_connection: bool | None = None


def load_headphones_state() -> HeadphonesState:
    if os.path.exists(HEADPHONES_STATE_PATH):
        try:
            with open(HEADPHONES_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return HeadphonesState(
                    mac=str(data.get("mac", "")),
                    name=str(data.get("name", TARGET_NAME)),
                    battery_pct=data.get("battery_pct"),
                    anc_mode=int(data.get("anc_mode", 0)),
                    eq_preset=int(data.get("eq_preset", 0x15)),
                    dual_connection=data.get("dual_connection"),
                )
        except (OSError, ValueError, TypeError):
            pass
    return HeadphonesState()


def save_headphones_state(state: HeadphonesState) -> None:
    try:
        with open(HEADPHONES_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f, indent=2)
    except OSError:
        pass


def find_connected_redmi_mac() -> str | None:
    """Procura o endereço MAC dos REDMI Headphones Neo ligados via bluetoothctl."""
    try:
        out = subprocess.check_output(
            ["bluetoothctl", "devices", "Connected"],
            text=True,
            timeout=3.0,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            # Formato: Device AA:BB:CC:DD:EE:FF REDMI Headphones Neo
            m = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", line.strip())
            if m and "redmi" in m.group(2).lower():
                return m.group(1).upper()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def read_bluez_battery(mac: str) -> int | None:
    """Lê a percentagem de bateria diretamente do BlueZ via bluetoothctl info."""
    if not mac:
        return None
    try:
        out = subprocess.check_output(
            ["bluetoothctl", "info", mac],
            text=True,
            timeout=3.0,
            stderr=subprocess.DEVNULL,
        )
        m = re.search(r"Battery Percentage:\s+0x[0-9a-fA-F]+\s+\((\d+)\)", out)
        if m:
            return max(0, min(100, int(m.group(1))))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


class RedmiHeadphonesDevice:
    """Cliente RFCOMM para os REDMI Headphones Neo."""

    def __init__(self) -> None:
        self.mac: str | None = None
        self.connected: bool = False
        self.rfcomm_busy: bool = False
        self._seq: int = 1

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq % 254) + 1
        return seq

    @staticmethod
    def _build_frame(flag: int, opcode: int, payload: bytes) -> bytes:
        length = len(payload)
        return (
            b"\xfe\xdc\xba"
            + bytes([flag, opcode, (length >> 8) & 0xFF, length & 0xFF])
            + payload
            + b"\xef"
        )

    @staticmethod
    def _parse_frames(raw: bytes) -> list[tuple[int, int, bytes]]:
        """Extrai frames FE DC BA <flag> <opcode> <len:2> <payload> EF."""
        frames: list[tuple[int, int, bytes]] = []
        i = 0
        n = len(raw)
        while i + 8 <= n:
            idx = raw.find(b"\xfe\xdc\xba", i)
            if idx < 0 or idx + 8 > n:
                break
            flag = raw[idx + 3]
            opcode = raw[idx + 4]
            plen = (raw[idx + 5] << 8) | raw[idx + 6]
            end = idx + 7 + plen
            if end < n and raw[end] == 0xEF:
                frames.append((flag, opcode, raw[idx + 7 : end]))
                i = end + 1
            else:
                i = idx + 3
        return frames

    def _rfcomm_exchange_batch(
        self, commands: list[tuple[int, bytes]], timeout: float = 2.5
    ) -> list[bytes | None]:
        """Abre uma única ligação RFCOMM ao canal 6 e executa uma lista de comandos."""
        results: list[bytes | None] = [None] * len(commands)
        mac = find_connected_redmi_mac()
        if not mac:
            self.mac = None
            self.connected = False
            self.rfcomm_busy = False
            return results

        self.mac = mac
        self.connected = True

        for attempt in range(2):
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(timeout)
            try:
                sock.connect((mac, RFCOMM_CHANNEL))
                self.rfcomm_busy = False
                buf = bytearray()
                for idx, (opcode, body) in enumerate(commands):
                    seq = self._next_seq()
                    frame = self._build_frame(0xC4, opcode, bytes([seq]) + body)
                    sock.sendall(frame)
                    matched: bytes | None = None
                    for _ in range(4):
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        buf.extend(chunk)
                        for flag, rx_op, payload in self._parse_frames(bytes(buf)):
                            if flag == 0x04 and rx_op == opcode and len(payload) >= 2 and payload[1] == seq:
                                matched = payload
                                break
                        if matched is not None:
                            break
                    results[idx] = matched
                return results
            except ConnectionRefusedError:
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                self.rfcomm_busy = True
                return results
            except (OSError, socket.timeout):
                return results
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
        return results

    def _rfcomm_exchange(self, opcode: int, body: bytes, timeout: float = 2.5) -> bytes | None:
        """Abre uma ligação RFCOMM curta ao canal 6, envia o comando e devolve a resposta."""
        return self._rfcomm_exchange_batch([(opcode, body)], timeout=timeout)[0]

    def get_device_info(self) -> tuple[str | None, int | None]:
        """Lê o nome e a percentagem de bateria via opcode 0x02 (com fallback BlueZ)."""
        payload = self._rfcomm_exchange(0x02, b"\xff\xff\xff\xff")
        name: str | None = None
        bat: int | None = None
        if payload and len(payload) >= 3 and payload[0] == 0x00:
            # TLVs após [status=0, seq]: [len_com_tipo, tipo, dados...]
            pos = 2
            while pos + 2 <= len(payload):
                tlen = payload[pos]
                if tlen < 1 or pos + 1 + tlen > len(payload):
                    break
                ttype = payload[pos + 1]
                tdata = payload[pos + 2 : pos + 1 + tlen]
                if ttype == 0x00:
                    name = tdata.decode("utf-8", errors="replace").strip("\x00")
                elif ttype == 0x02 and len(tdata) >= 1:
                    bat = int(tdata[0])
                elif ttype == 0x07 and len(tdata) >= 2 and bat is None:
                    bat = int(tdata[1])
                pos += 1 + tlen
        if bat is None and self.mac:
            bat = read_bluez_battery(self.mac)
        return name, bat

    def _get_attr(self, attr_id: int) -> bytes | None:
        payload = self._rfcomm_exchange(0xF3, bytes([(attr_id >> 8) & 0xFF, attr_id & 0xFF]))
        if not payload or len(payload) < 5 or payload[0] != 0x00:
            return None
        # Formato: [status, seq, len, attr_hi, attr_lo, val...]
        tlen = payload[2]
        if tlen < 2 or 3 + tlen > len(payload):
            return None
        return payload[5 : 3 + tlen]

    def get_anc_mode(self) -> int | None:
        val = self._get_attr(0x000B)
        if val and len(val) >= 1:
            return int(val[0])
        return None

    def set_anc_mode(self, mode: int) -> bool:
        if mode not in ANC_MODES:
            return False
        payload = self._rfcomm_exchange(0x08, bytes([0x02, 0x04, mode]))
        return bool(payload and payload[0] == 0x00)

    def get_eq_preset(self) -> int | None:
        val = self._get_attr(0x0007)
        if val and len(val) >= 1:
            return int(val[0])
        return None

    def set_eq_preset(self, preset: int) -> bool:
        payload = self._rfcomm_exchange(0xF2, bytes([0x03, 0x00, 0x07, preset & 0xFF]))
        return bool(payload and payload[0] == 0x00)

    def get_dual_connection(self) -> bool | None:
        val = self._get_attr(0x0004)
        if val and len(val) >= 1:
            return bool(val[0])
        return None

    def set_dual_connection(self, enabled: bool) -> bool:
        payload = self._rfcomm_exchange(0xF2, bytes([0x03, 0x00, 0x04, 1 if enabled else 0]))
        return bool(payload and payload[0] == 0x00)

    def apply_anc_and_eq(self, anc_mode: int, eq_preset: int) -> tuple[bool, bool]:
        """Aplica ANC e EQ numa única ligação RFCOMM para evitar conflito de reconexão."""
        if anc_mode not in ANC_MODES:
            return False, self.set_eq_preset(eq_preset)
        res = self._rfcomm_exchange_batch(
            [
                (0x08, bytes([0x02, 0x04, anc_mode])),
                (0xF2, bytes([0x03, 0x00, 0x07, eq_preset & 0xFF])),
            ]
        )
        ok_anc = bool(res[0] and res[0][0] == 0x00)
        ok_eq = bool(res[1] and res[1][0] == 0x00)
        return ok_anc, ok_eq


def _bluez_card_name(mac: str) -> str:
    return f"bluez_card.{mac.replace(':', '_').upper()}"


def get_audio_profiles(mac: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Devolve (perfil_ativo, [(id_perfil, nome_legivel), ...]) via pactl."""
    if not mac:
        return None, []
    card = _bluez_card_name(mac)
    try:
        out = subprocess.check_output(
            ["pactl", "list", "cards"],
            text=True,
            timeout=3.0,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None, []

    blocks = out.split("Card #") if "Card #" in out else out.split("Placa #")
    target_block = ""
    for blk in blocks:
        if card in blk:
            target_block = blk
            break
    if not target_block:
        return None, []

    active: str | None = None
    profiles: list[tuple[str, str]] = []
    in_profiles = False
    for raw_line in target_block.splitlines():
        line = raw_line.strip()
        m_act = re.match(r"^(?:Active Profile|Perfil Activo|Perfil Ativo):\s*(\S+)", line)
        if m_act:
            active = m_act.group(1)
            in_profiles = False
            continue
        if re.match(r"^(?:Profiles|Perfis):", line):
            in_profiles = True
            continue
        if re.match(r"^(?:Ports|Portas):", line):
            in_profiles = False
            continue
        if in_profiles:
            m_prof = re.match(r"^([a-zA-Z0-9_.-]+):\s+(.+)$", line)
            if m_prof:
                pid = m_prof.group(1)
                if pid == "off":
                    continue
                label = AUDIO_PROFILE_LABELS.get(pid, pid)
                profiles.append((pid, label))
    return active, profiles


def set_audio_profile(mac: str, profile_id: str) -> bool:
    """Altera o perfil PipeWire/PulseAudio (A2DP / HFP) dos auscultadores."""
    if not mac or not profile_id:
        return False
    try:
        subprocess.check_call(
            ["pactl", "set-card-profile", _bluez_card_name(mac), profile_id],
            timeout=3.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False
