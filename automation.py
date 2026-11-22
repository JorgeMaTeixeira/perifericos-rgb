#!/usr/bin/env python3
"""Cálculo solar offline (Maia, Portugal) e serviço silencioso de automação RGB."""

from __future__ import annotations

import json
import logging
import math
import os
import select
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

from glorious_hid import (
    GloriousMouseDevice,
    find_glorious_mouse_event,
    load_mouse_state,
    save_mouse_state,
)
from via_hid import KeychronRGBDevice

log = logging.getLogger("keychron-auto")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "automation_config.json")
PROFILES_PATH = os.path.join(BASE_DIR, "profiles.json")
KB_STATE_PATH = os.path.join(BASE_DIR, "keyboard_state.json")
SERVICE_NAME = "keychron-rgb-auto.service"

# Coordenadas de Maia, Portugal
MAIA_LAT = 41.2357
MAIA_LON = -8.6199

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "mode": "sunset",  # "sunset" ou "fixed"
    "start_time": "18:00",
    "end_time": "07:30",
    "night_profile": "Noite (Âmbar 20%)",
    "turn_off_during_day": True,
    "turn_off_on_lock": True,
    "control_keyboard": True,
    "control_mouse": True,
    "battery_saver_enabled": False,
    "battery_saver_threshold": 20,
    "battery_saver_brightness_pct": 15,
}


def _solar_event_utc(day: date, lat: float, lon: float, is_sunrise: bool) -> datetime:
    """Calcula nascer ou pôr do sol em UTC usando a aproximação NOAA."""
    n = day.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = n + ((6.0 - lng_hour) / 24.0 if is_sunrise else (18.0 - lng_hour) / 24.0)

    m = (0.9856 * t) - 3.289
    l = m + (1.916 * math.sin(math.radians(m))) + (0.020 * math.sin(math.radians(2 * m))) + 282.634
    l %= 360.0

    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l)))) % 360.0
    l_quad = (math.floor(l / 90.0)) * 90.0
    ra_quad = (math.floor(ra / 90.0)) * 90.0
    ra = (ra + (l_quad - ra_quad)) / 15.0

    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))

    zenith = 90.833
    cos_h = (math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(lat)))) / (
        cos_dec * math.cos(math.radians(lat))
    )
    cos_h = max(-1.0, min(1.0, cos_h))

    h = 360.0 - math.degrees(math.acos(cos_h)) if is_sunrise else math.degrees(math.acos(cos_h))
    h /= 15.0

    t_local = h + ra - (0.06571 * t) - 6.622
    ut = (t_local - lng_hour) % 24.0
    hours = int(ut)
    minutes = int((ut - hours) * 60)
    seconds = int((((ut - hours) * 60) - minutes) * 60)
    return datetime(day.year, day.month, day.day, hours, minutes, seconds, tzinfo=timezone.utc)


def get_sun_times(day: date | None = None) -> tuple[datetime, datetime]:
    """Devolve (nascer_do_sol, por_do_sol) no fuso horário local do sistema."""
    d = day or datetime.now().date()
    sunrise = _solar_event_utc(d, MAIA_LAT, MAIA_LON, is_sunrise=True).astimezone()
    sunset = _solar_event_utc(d, MAIA_LAT, MAIA_LON, is_sunrise=False).astimezone()
    return sunrise, sunset


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_profile(name: str) -> dict[str, int] | None:
    if not os.path.exists(PROFILES_PATH):
        return None
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            profiles = json.load(f)
        prof = profiles.get(name)
        if isinstance(prof, dict):
            return prof
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _parse_hhmm(hhmm: str, ref: datetime) -> datetime:
    try:
        h, m = map(int, hhmm.strip().split(":"))
        return ref.replace(hour=h, minute=m, second=0, microsecond=0)
    except Exception:
        return ref.replace(hour=18, minute=0, second=0, microsecond=0)


def is_night_now(cfg: dict, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now().astimezone()
    mode = cfg.get("mode", "sunset")
    if mode == "fixed":
        start = _parse_hhmm(cfg.get("start_time", "18:00"), now)
        end = _parse_hhmm(cfg.get("end_time", "07:30"), now)
        if start <= end:
            night = start <= now < end
        else:
            night = now >= start or now < end
        return night, f"Horário {start.strftime('%H:%M')}–{end.strftime('%H:%M')}"

    sunrise, sunset = get_sun_times(now.date())
    night = now < sunrise or now >= sunset
    return night, f"Nascer {sunrise.strftime('%H:%M')} · Pôr do sol {sunset.strftime('%H:%M')}"


def is_screen_locked() -> bool:
    try:
        res = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.ScreenSaver",
                "--object-path",
                "/org/gnome/ScreenSaver",
                "--method",
                "org.gnome.ScreenSaver.GetActive",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return "true" in res.stdout.lower()
    except Exception:
        return False


def _send_notification(icon: str, title: str, body: str) -> None:
    try:
        subprocess.Popen(
            ["notify-send", "-u", "normal", "-i", icon, title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def notify_low_battery(device_name: str, pct: int) -> None:
    """Envia uma notificação nativa do sistema para bateria fraca."""
    _send_notification(
        "battery-low",
        f"Bateria fraca — {device_name}",
        f"Bateria a {pct}%. Liga o cabo USB-C para carregar.",
    )


def notify_full_battery(device_name: str) -> None:
    """Envia uma notificação nativa quando a carga chega a 100%."""
    _send_notification(
        "battery-full-charged",
        f"Carga completa — {device_name}",
        "Bateria a 100%. Já podes desligar o cabo USB-C.",
    )


def is_service_active() -> bool:
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return res.stdout.strip() == "active"
    except Exception:
        return False


def set_service_enabled(enabled: bool) -> tuple[bool, str]:
    action = ["enable", "--now"] if enabled else ["disable", "--now"]
    try:
        res = subprocess.run(
            ["systemctl", "--user", *action, SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return False, res.stderr.strip() or "Erro ao gerir serviço systemd"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _load_kb_battery_pct() -> int | None:
    if os.path.exists(KB_STATE_PATH):
        try:
            with open(KB_STATE_PATH, "r", encoding="utf-8") as f:
                pct = json.load(f).get("battery_pct")
            if isinstance(pct, int) and 0 <= pct <= 100:
                return pct
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return None


def _apply_keyboard(desired: str, cfg: dict) -> bool:
    """Aplica 'on' ou 'off' ao teclado. Devolve True apenas se comunicou com sucesso."""
    dev = KeychronRGBDevice()
    try:
        dev.connect()
        if desired == "off":
            dev.set_effect(0)
        else:
            prof = load_profile(cfg.get("night_profile", "")) or {
                "effect": 1,
                "brightness": 51,
                "speed": 127,
                "hue": 21,
                "saturation": 255,
            }
            bri = int(prof.get("brightness", 51))
            if cfg.get("battery_saver_enabled", False) and dev.connection_mode != "USB-C":
                kb_pct = _load_kb_battery_pct()
                threshold = int(cfg.get("battery_saver_threshold", 20))
                if kb_pct is not None and kb_pct <= threshold:
                    saver_raw = round(int(cfg.get("battery_saver_brightness_pct", 15)) * 255 / 100)
                    bri = min(bri, saver_raw)
            dev.set_effect(int(prof.get("effect", 1)))
            dev.set_brightness(bri)
            dev.set_speed(int(prof.get("speed", 127)))
            dev.set_color(int(prof.get("hue", 21)), int(prof.get("saturation", 255)))
        log.info("Teclado: aplicado '%s' (%s)", desired, dev.connection_mode)
        return True
    except Exception as exc:
        log.warning("Teclado: falha ao aplicar '%s' (%s)", desired, exc)
        return False
    finally:
        dev.close()


def _apply_mouse(desired: str, cfg: dict | None = None) -> tuple[bool, str]:
    """Aplica 'on' ou 'off' ao rato. Devolve (sucesso, conn_type)."""
    cfg = cfg or {}
    mdev = GloriousMouseDevice()
    try:
        mdev.connect()
        conn_type = mdev.conn_type
        st = load_mouse_state()
        if desired == "off":
            mdev.set_lighting(0, 0, st.speed, st.color)
            if st.effect > 0:
                st.last_active_effect = st.effect
            st.effect = 0
            save_mouse_state(st)
        else:
            prof = load_profile(cfg.get("night_profile", ""))
            mprof = prof.get("mouse") if isinstance(prof, dict) else None
            if isinstance(mprof, dict):
                eff = int(mprof.get("effect", st.last_active_effect or 4))
                bri = int(mprof.get("brightness", 50))
                spd = int(mprof.get("speed", st.speed))
                raw_col = mprof.get("color", list(st.color))
                col = (int(raw_col[0]), int(raw_col[1]), int(raw_col[2]))
            else:
                eff = st.effect if st.effect > 0 else (st.last_active_effect or 4)
                bri = st.brightness if st.brightness > 0 else 50
                spd = st.speed
                col = st.color
            applied_bri = bri
            if cfg.get("battery_saver_enabled", False) and conn_type == "receiver":
                threshold = int(cfg.get("battery_saver_threshold", 20))
                if st.battery_pct is not None and st.battery_pct <= threshold:
                    saver_bri = int(cfg.get("battery_saver_brightness_pct", 15))
                    applied_bri = min(bri, saver_bri)
            mdev.set_lighting(eff, applied_bri, spd, col)
            st.effect = eff
            if eff > 0:
                st.last_active_effect = eff
            st.brightness = bri
            st.speed = spd
            st.color = col
            save_mouse_state(st)
        log.info("Rato: aplicado '%s' (%s)", desired, conn_type)
        return True, conn_type
    except Exception as exc:
        log.warning("Rato: falha ao aplicar '%s' (%s)", desired, exc)
        return False, ""
    finally:
        mdev.close()


def _apply_desired_state(
    desired: str, cfg: dict, targets: set[str] | None = None
) -> dict[str, tuple[bool, str]]:
    """Aplica 'on' ou 'off' aos periféricos pedidos e devolve (ok, modo) por dispositivo."""
    wanted = targets if targets is not None else {"keyboard", "mouse"}
    results: dict[str, tuple[bool, str]] = {}

    if "keyboard" in wanted and cfg.get("control_keyboard", True):
        results["keyboard"] = (_apply_keyboard(desired, cfg), "")

    if "mouse" in wanted and cfg.get("control_mouse", True):
        results["mouse"] = _apply_mouse(desired, cfg)

    return results


def _acquire_logind_inhibit(bus: object, Gio: object, GLib: object) -> int | None:
    """Pede um delay inhibitor ao systemd-logind para shutdown e suspensão."""
    try:
        res, fd_list = bus.call_with_unix_fd_list_sync(
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            "Inhibit",
            GLib.Variant(
                "(ssss)",
                (
                    "shutdown:sleep",
                    "KeychronRGB",
                    "Apagar LEDs dos periféricos antes de desligar ou suspender",
                    "delay",
                ),
            ),
            GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,
        )
        if fd_list is None:
            return None
        idx = res.unpack()[0]
        return int(fd_list.steal_fds()[idx])
    except Exception:
        return None


def run_daemon() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    log.info("Serviço de automação iniciado.")

    wake_event = threading.Event()
    state_lock = threading.Lock()
    power_state = {"off_pending": False, "force_reapply": False}
    last_applied: dict[str, str | None] = {"keyboard": None, "mouse": None}
    mouse_activity = {"last_ts": 0.0, "pending_wake_reapply": False}

    def monitor_mouse_activity() -> None:
        """Deteta movimento/clique no rato 2.4G para reaplicar iluminação ao acordar."""
        while True:
            ev_path = find_glorious_mouse_event()
            if not ev_path:
                time.sleep(2.0)
                continue
            fd: int | None = None
            try:
                fd = os.open(ev_path, os.O_RDONLY | os.O_NONBLOCK)
                while True:
                    ready, _, _ = select.select([fd], [], [], 2.0)
                    if not ready:
                        continue
                    got_data = False
                    while True:
                        try:
                            chunk = os.read(fd, 4096)
                            if not chunk:
                                raise OSError("EOF")
                            got_data = True
                        except BlockingIOError:
                            break
                    if not got_data:
                        continue
                    mouse_activity["last_ts"] = time.monotonic()
                    if mouse_activity["pending_wake_reapply"]:
                        mouse_activity["pending_wake_reapply"] = False
                        time.sleep(0.3)
                        with state_lock:
                            if not power_state["off_pending"]:
                                last_applied["mouse"] = None
                        log.info("Rato: atividade detetada após inatividade; a reaplicar iluminação.")
                        wake_event.set()
            except OSError:
                time.sleep(2.0)
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def monitor_screensaver() -> None:
        try:
            proc = subprocess.Popen(
                [
                    "gdbus",
                    "monitor",
                    "--session",
                    "--dest",
                    "org.gnome.ScreenSaver",
                    "--object-path",
                    "/org/gnome/ScreenSaver",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            if proc.stdout:
                for line in proc.stdout:
                    if "ActiveChanged" in line:
                        wake_event.set()
        except Exception:
            pass

    def monitor_logind() -> None:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception:
            return

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            return

        inhibit_fd: int | None = _acquire_logind_inhibit(bus, Gio, GLib)

        def on_prepare(_conn: object, _sender: str, _path: str, _iface: str, _signal: str, params: object) -> None:
            nonlocal inhibit_fd
            going_down = bool(params.unpack()[0])
            if going_down:
                power_state["off_pending"] = True
                cfg = load_config()
                if cfg.get("enabled", True):
                    with state_lock:
                        results = _apply_desired_state("off", cfg)
                        for dev_name, (ok, _conn) in results.items():
                            if ok:
                                last_applied[dev_name] = "off"
                if inhibit_fd is not None:
                    try:
                        os.close(inhibit_fd)
                    except OSError:
                        pass
                    inhibit_fd = None
            else:
                power_state["off_pending"] = False
                power_state["force_reapply"] = True
                if inhibit_fd is None:
                    inhibit_fd = _acquire_logind_inhibit(bus, Gio, GLib)
                wake_event.set()

        for sig_name in ("PrepareForShutdown", "PrepareForSleep"):
            bus.signal_subscribe(
                "org.freedesktop.login1",
                "org.freedesktop.login1.Manager",
                sig_name,
                "/org/freedesktop/login1",
                None,
                Gio.DBusSignalFlags.NONE,
                on_prepare,
            )

        GLib.MainLoop().run()

    threading.Thread(target=monitor_screensaver, daemon=True).start()
    threading.Thread(target=monitor_logind, daemon=True).start()
    threading.Thread(target=monitor_mouse_activity, daemon=True).start()

    last_mtime: float = 0.0

    while True:
        cfg = load_config()
        try:
            mtime = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else 0.0
        except OSError:
            mtime = 0.0

        config_changed = mtime != last_mtime
        if config_changed or power_state["force_reapply"]:
            last_mtime = mtime
            last_applied = {"keyboard": None, "mouse": None}
            mouse_activity["pending_wake_reapply"] = False
            power_state["force_reapply"] = False

        still_pending = False
        if cfg.get("enabled", True) and not power_state["off_pending"]:
            locked = cfg.get("turn_off_on_lock", True) and is_screen_locked()
            night, _ = is_night_now(cfg)

            if locked:
                desired = "off"
            elif night:
                desired = "on"
            elif cfg.get("turn_off_during_day", True):
                desired = "off"
            else:
                desired = None

            for dev_name in ("keyboard", "mouse"):
                if not cfg.get(f"control_{dev_name}", True):
                    last_applied[dev_name] = None
                    if dev_name == "mouse":
                        mouse_activity["pending_wake_reapply"] = False

            if desired is not None:
                pending = {
                    dev_name
                    for dev_name in ("keyboard", "mouse")
                    if cfg.get(f"control_{dev_name}", True) and last_applied[dev_name] != desired
                }
                if pending:
                    with state_lock:
                        if not power_state["off_pending"]:
                            results = _apply_desired_state(desired, cfg, targets=pending)
                            for dev_name, (ok, conn_type) in results.items():
                                if ok:
                                    last_applied[dev_name] = desired
                                    if dev_name == "mouse":
                                        idle_sec = time.monotonic() - mouse_activity["last_ts"]
                                        if conn_type == "receiver" and idle_sec >= 3.0:
                                            mouse_activity["pending_wake_reapply"] = True
                                            log.info(
                                                "Rato: enviado via 2.4G sem atividade recente; "
                                                "será confirmado ao mexer no rato."
                                            )
                                        else:
                                            mouse_activity["pending_wake_reapply"] = False

                still_pending = any(
                    cfg.get(f"control_{dev_name}", True) and last_applied[dev_name] != desired
                    for dev_name in ("keyboard", "mouse")
                )

        wake_event.wait(timeout=5.0 if still_pending else 30.0)
        wake_event.clear()


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        raise SystemExit(run_daemon())
    sr, ss = get_sun_times()
    print(f"Maia, PT -> Nascer: {sr.strftime('%H:%M')}, Pôr do sol: {ss.strftime('%H:%M')}")
