"""Registo leve de histórico de bateria e estimativa de autonomia."""

from __future__ import annotations

import json
import os
import time

from shared_utils import atomic_write_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(BASE_DIR, "battery_history.json")

MAX_SAMPLES = 200
MIN_SAMPLE_INTERVAL_SEC = 1800  # 30 minutos (registo periódico)
MIN_CHANGE_INTERVAL_SEC = 300   # 5 minutos mínimos entre registos de variação


def _load_history() -> dict:
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_history(data: dict) -> None:
    try:
        atomic_write_json(HISTORY_PATH, data)
    except OSError:
        pass


def record_battery(device_key: str, pct: int, is_charging: bool) -> None:
    """Regista uma leitura de bateria e atualiza a marca de última carga."""
    now = time.time()
    data = _load_history()
    entry = data.get(device_key)
    if not isinstance(entry, dict):
        entry = {"samples": []}

    prev_pct = entry.get("last_pct")
    samples = entry.get("samples")
    if not isinstance(samples, list):
        samples = []

    # Deteta carga completa ou aumento significativo de carga
    if (is_charging and pct >= 95) or (
        isinstance(prev_pct, int) and pct >= prev_pct + 10
    ):
        entry["last_charged_ts"] = now
    elif pct == 100 and "last_charged_ts" not in entry:
        entry["last_charged_ts"] = now

    should_append = False
    if not samples:
        should_append = True
    else:
        last_s = samples[-1]
        dt = now - float(last_s.get("ts", 0))
        dp = abs(pct - int(last_s.get("pct", pct)))
        if (
            bool(last_s.get("charging")) != is_charging
            or dt >= MIN_SAMPLE_INTERVAL_SEC
            or (dp >= 2 and dt >= MIN_CHANGE_INTERVAL_SEC)
        ):
            should_append = True

    if should_append:
        samples.append({"ts": round(now, 1), "pct": int(pct), "charging": bool(is_charging)})
        if len(samples) > MAX_SAMPLES:
            samples = samples[-MAX_SAMPLES:]

    entry["last_pct"] = int(pct)
    entry["last_seen_ts"] = round(now, 1)
    entry["samples"] = samples
    data[device_key] = entry
    _save_history(data)


def _format_duration(seconds: float) -> str:
    secs = max(0, int(seconds))
    if secs < 90:
        return "agora mesmo"
    mins = secs // 60
    if mins < 60:
        return f"há {mins} min"
    hours = mins // 60
    if hours < 24:
        rem_m = mins % 60
        return f"há {hours}h {rem_m}m" if rem_m >= 5 else f"há {hours}h"
    days = hours // 24
    rem_h = hours % 24
    return f"há {days}d {rem_h}h" if rem_h > 0 else f"há {days}d"


def _estimate_remaining_hours(samples: list[dict], current_pct: int) -> float | None:
    """Estima horas restantes com base na descarga desde a última carga."""
    # Percorre de trás para a frente enquanto não houver recarga
    drain_pts: list[tuple[float, int]] = []
    for s in reversed(samples):
        if s.get("charging"):
            break
        ts = float(s.get("ts", 0))
        pct = int(s.get("pct", 0))
        if drain_pts and pct < drain_pts[-1][1] - 5:
            # Salto ascendente no passado -> houve carga antes disto
            break
        drain_pts.append((ts, pct))

    if len(drain_pts) < 2:
        return None
    newest_ts, newest_pct = drain_pts[0]
    oldest_ts, oldest_pct = drain_pts[-1]
    dt_hours = (newest_ts - oldest_ts) / 3600.0
    dp = oldest_pct - newest_pct
    if dt_hours < 2.0 or dp < 3:
        return None
    rate_per_hour = dp / dt_hours
    if rate_per_hour <= 0:
        return None
    return current_pct / rate_per_hour


def get_device_history(device_key: str) -> dict:
    """Devolve o dicionário de histórico de um dispositivo."""
    data = _load_history()
    entry = data.get(device_key)
    if isinstance(entry, dict):
        return entry
    return {"samples": []}


def clear_device_history(device_key: str) -> None:
    """Limpa as amostras registadas de um dispositivo."""
    data = _load_history()
    if device_key in data:
        del data[device_key]
        _save_history(data)


def format_battery_summary(
    device_key: str, current_pct: int | None, is_charging: bool
) -> str:
    """Devolve um resumo curto em português para mostrar no cartão de bateria."""
    data = _load_history()
    entry = data.get(device_key)
    if not isinstance(entry, dict):
        return ""

    parts: list[str] = []
    last_charged = entry.get("last_charged_ts")
    if isinstance(last_charged, (int, float)) and last_charged > 0:
        age = time.time() - float(last_charged)
        parts.append(f"Última carga {_format_duration(age)}")

    if not is_charging and current_pct is not None and current_pct > 0:
        samples = entry.get("samples", [])
        if isinstance(samples, list):
            est_h = _estimate_remaining_hours(samples, current_pct)
            if est_h is not None:
                if est_h >= 48:
                    parts.append(f"~{est_h / 24:.1f} dias restantes")
                elif est_h >= 1:
                    parts.append(f"~{round(est_h)}h restantes")

    return " · ".join(parts)
