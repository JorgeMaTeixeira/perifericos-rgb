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
from shared_utils import atomic_write_json, find_input_events
from via_hid import DONGLE_PRODUCT_ID as KB_DONGLE_PID
from via_hid import PRODUCT_ID as KB_USB_PID
from via_hid import VENDOR_ID as KB_VID
from via_hid import KeychronRGBDevice

log = logging.getLogger("keychron-auto")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "automation_config.json")
PROFILES_PATH = os.path.join(BASE_DIR, "profiles.json")
KB_STATE_PATH = os.path.join(BASE_DIR, "keyboard_state.json")
OVERRIDE_PATH = os.path.join(BASE_DIR, "automation_override.json")
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
    "low_battery_alert": True,
    "low_battery_threshold": 20,
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
    atomic_write_json(CONFIG_PATH, cfg, ensure_ascii=False)


def get_current_phase(cfg: dict, now: datetime | None = None) -> str:
    night, _ = is_night_now(cfg, now=now)
    return "night" if night else "day"


def set_manual_override(desired: str | None, cfg: dict | None = None) -> None:
    """Regista ou limpa um override manual válido apenas até à próxima transição dia/noite."""
    if desired is None:
        try:
            os.remove(OVERRIDE_PATH)
        except FileNotFoundError:
            pass
        return
    cfg = cfg or load_config()
    atomic_write_json(
        OVERRIDE_PATH,
        {"desired": desired, "phase": get_current_phase(cfg), "ts": time.time()},
        ensure_ascii=False,
    )


def load_manual_override() -> dict | None:
    if not os.path.exists(OVERRIDE_PATH):
        return None
    try:
        with open(OVERRIDE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("desired") in ("on", "off"):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


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


def _apply_keyboard(
    desired: str,
    cfg: dict,
    persist_eeprom: bool = False,
    force_eeprom_sync: bool = False,
) -> tuple[bool, str]:
    """Aplica 'on' ou 'off' ao teclado. Devolve (sucesso, connection_mode)."""
    dev = KeychronRGBDevice()
    try:
        dev.connect()
        state = dev.get_state()
        if desired == "off":
            if state.effect != 0 or (persist_eeprom and force_eeprom_sync):
                if persist_eeprom:
                    dev.disable_and_save()
                else:
                    dev.set_effect(0)
            else:
                log.debug("Teclado: já desligado (%s)", dev.connection_mode)
                return True, dev.connection_mode
        else:
            prof = load_profile(cfg.get("night_profile", "")) or {
                "effect": 1,
                "brightness": 51,
                "speed": 127,
                "hue": 21,
                "saturation": 255,
            }
            eff = int(prof.get("effect", 1))
            base_bri = int(prof.get("brightness", 51))
            spd = int(prof.get("speed", 127))
            hue = int(prof.get("hue", 21))
            sat = int(prof.get("saturation", 255))

            bri = base_bri
            saver_active = False
            if cfg.get("battery_saver_enabled", False) and dev.connection_mode != "USB-C":
                kb_pct = _load_kb_battery_pct()
                threshold = int(cfg.get("battery_saver_threshold", 20))
                if kb_pct is not None and kb_pct <= threshold:
                    saver_raw = round(int(cfg.get("battery_saver_brightness_pct", 15)) * 255 / 100)
                    if saver_raw < bri:
                        bri = saver_raw
                        saver_active = True

            needs_ram_update = (
                state.effect != eff
                or state.brightness != bri
                or state.speed != spd
                or state.hue != hue
                or state.saturation != sat
            )
            if persist_eeprom and force_eeprom_sync and not saver_active:
                dev.apply_and_save_rgb(eff, bri, spd, hue, sat)
            elif needs_ram_update:
                dev.set_effect(eff)
                dev.set_brightness(bri)
                dev.set_speed(spd)
                dev.set_color(hue, sat)
            else:
                log.debug("Teclado: já no estado pretendido (%s)", dev.connection_mode)
                return True, dev.connection_mode

        log.info(
            "Teclado: aplicado '%s'%s (%s)",
            desired,
            " + EEPROM" if (persist_eeprom and force_eeprom_sync) else "",
            dev.connection_mode,
        )
        return True, dev.connection_mode
    except Exception as exc:
        log.debug("Teclado: falha ao aplicar '%s' (%s)", desired, exc)
        return False, str(exc)
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
    desired: str,
    cfg: dict,
    targets: set[str] | None = None,
    persist_kb_eeprom: bool = False,
    force_kb_eeprom_sync: bool = False,
) -> dict[str, tuple[bool, str]]:
    """Aplica 'on' ou 'off' aos periféricos pedidos e devolve (ok, info) por dispositivo."""
    wanted = targets if targets is not None else {"keyboard", "mouse"}
    results: dict[str, tuple[bool, str]] = {}

    if "keyboard" in wanted and cfg.get("control_keyboard", True):
        results["keyboard"] = _apply_keyboard(
            desired,
            cfg,
            persist_eeprom=persist_kb_eeprom,
            force_eeprom_sync=force_kb_eeprom_sync,
        )

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
    power_state = {"off_pending": False, "force_reapply": False, "screen_locked": is_screen_locked()}
    last_applied: dict[str, str | None] = {"keyboard": None, "mouse": None}
    mouse_activity = {"last_ts": 0.0, "pending_wake_reapply": False}
    kb_activity = {"last_ts": 0.0}
    warned_offline: dict[str, bool] = {"keyboard": False, "mouse": False}

    def _drain_fd(fd: int) -> bool:
        got_data = False
        while True:
            try:
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise OSError("EOF")
                got_data = True
            except BlockingIOError:
                break
        return got_data

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
                    if not _drain_fd(fd):
                        continue
                    now_m = time.monotonic()
                    idle = now_m - mouse_activity["last_ts"]
                    mouse_activity["last_ts"] = now_m
                    if mouse_activity["pending_wake_reapply"] or idle >= 180.0:
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

    def monitor_keyboard_activity() -> None:
        """Deteta teclas no Keychron (2.4G ou USB-C) para aplicar imediatamente ao acordar."""
        while True:
            ev_paths = find_input_events(KB_VID, (KB_USB_PID, KB_DONGLE_PID))
            if not ev_paths:
                time.sleep(2.0)
                continue
            fds: list[int] = []
            try:
                for p in ev_paths:
                    try:
                        fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
                    except OSError:
                        pass
                if not fds:
                    time.sleep(2.0)
                    continue
                while True:
                    ready, _, _ = select.select(fds, [], [], 2.0)
                    if not ready:
                        continue
                    got_data = False
                    for rfd in ready:
                        if _drain_fd(rfd):
                            got_data = True
                    if not got_data:
                        continue
                    now_m = time.monotonic()
                    idle = now_m - kb_activity["last_ts"]
                    kb_activity["last_ts"] = now_m
                    # Se o teclado estava offline ou esteve inativo >= 10s, acorda o daemon
                    if warned_offline["keyboard"] or idle >= 10.0 or last_applied["keyboard"] is None:
                        time.sleep(0.15)
                        with state_lock:
                            if not power_state["off_pending"] and idle >= 180.0:
                                last_applied["keyboard"] = None
                        wake_event.set()
            except OSError:
                time.sleep(2.0)
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def monitor_dbus() -> None:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception:
            return

        try:
            sys_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            sys_bus = None

        try:
            sess_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            sess_bus = None

        inhibit_fd: int | None = (
            _acquire_logind_inhibit(sys_bus, Gio, GLib) if sys_bus is not None else None
        )

        def on_prepare(
            _conn: object, _sender: str, _path: str, _iface: str, _signal: str, params: object
        ) -> None:
            nonlocal inhibit_fd
            going_down = bool(params.unpack()[0])
            if going_down:
                power_state["off_pending"] = True
                cfg = load_config()
                if cfg.get("enabled", True):
                    with state_lock:
                        results = _apply_desired_state("off", cfg)
                        for dev_name, (ok, _info) in results.items():
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
                if inhibit_fd is None and sys_bus is not None:
                    inhibit_fd = _acquire_logind_inhibit(sys_bus, Gio, GLib)
                wake_event.set()

        def on_screensaver_active(
            _conn: object, _sender: str, _path: str, _iface: str, _signal: str, params: object
        ) -> None:
            power_state["screen_locked"] = bool(params.unpack()[0])
            wake_event.set()

        if sys_bus is not None:
            for sig_name in ("PrepareForShutdown", "PrepareForSleep"):
                sys_bus.signal_subscribe(
                    "org.freedesktop.login1",
                    "org.freedesktop.login1.Manager",
                    sig_name,
                    "/org/freedesktop/login1",
                    None,
                    Gio.DBusSignalFlags.NONE,
                    on_prepare,
                )

        if sess_bus is not None:
            sess_bus.signal_subscribe(
                "org.gnome.ScreenSaver",
                "org.gnome.ScreenSaver",
                "ActiveChanged",
                "/org/gnome/ScreenSaver",
                None,
                Gio.DBusSignalFlags.NONE,
                on_screensaver_active,
            )

        GLib.MainLoop().run()

    threading.Thread(target=monitor_dbus, daemon=True).start()
    threading.Thread(target=monitor_mouse_activity, daemon=True).start()
    threading.Thread(target=monitor_keyboard_activity, daemon=True).start()

    def _safe_mtime(path: str) -> float:
        try:
            return os.path.getmtime(path) if os.path.exists(path) else 0.0
        except OSError:
            return 0.0

    last_mtimes = (0.0, 0.0, 0.0)
    eeprom_synced_key: tuple[str, str] | None = None
    retry_step = 0
    backoff_schedule = (5.0, 10.0, 20.0, 30.0, 60.0)

    while True:
        cfg = load_config()
        mtimes = (
            _safe_mtime(CONFIG_PATH),
            _safe_mtime(PROFILES_PATH),
            _safe_mtime(OVERRIDE_PATH),
        )
        files_changed = mtimes != last_mtimes
        if files_changed or power_state["force_reapply"]:
            last_mtimes = mtimes
            last_applied = {"keyboard": None, "mouse": None}
            mouse_activity["pending_wake_reapply"] = False
            power_state["force_reapply"] = False
            retry_step = 0
            if files_changed:
                eeprom_synced_key = None

        still_pending = False
        if cfg.get("enabled", True) and not power_state["off_pending"]:
            locked = cfg.get("turn_off_on_lock", True) and bool(
                power_state["screen_locked"] or is_screen_locked()
            )
            phase = get_current_phase(cfg)
            night = phase == "night"

            override = load_manual_override()
            if override and override.get("phase") != phase:
                set_manual_override(None)
                override = None

            use_override = False
            if locked:
                desired = "off"
                set_manual_override(None)
            elif override is not None:
                desired = str(override["desired"])
                use_override = True
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
                # Só grava na EEPROM do teclado em transições normais Dia/Noite (nunca em lock/override)
                persist_kb_eeprom = (not locked) and (not use_override)
                sync_key = (phase, desired)
                force_kb_eeprom = persist_kb_eeprom and (eeprom_synced_key != sync_key)

                pending = {
                    dev_name
                    for dev_name in ("keyboard", "mouse")
                    if cfg.get(f"control_{dev_name}", True) and last_applied[dev_name] != desired
                }
                if pending:
                    with state_lock:
                        if not power_state["off_pending"]:
                            results = _apply_desired_state(
                                desired,
                                cfg,
                                targets=pending,
                                persist_kb_eeprom=persist_kb_eeprom,
                                force_kb_eeprom_sync=force_kb_eeprom,
                            )
                            for dev_name, (ok, info) in results.items():
                                if ok:
                                    if warned_offline[dev_name]:
                                        log.info(
                                            "%s: voltou a responder; estado '%s' aplicado.",
                                            "Teclado" if dev_name == "keyboard" else "Rato",
                                            desired,
                                        )
                                        warned_offline[dev_name] = False
                                    last_applied[dev_name] = desired
                                    if dev_name == "keyboard" and persist_kb_eeprom:
                                        eeprom_synced_key = sync_key
                                    if dev_name == "mouse":
                                        idle_sec = time.monotonic() - mouse_activity["last_ts"]
                                        if info == "receiver" and idle_sec >= 3.0:
                                            mouse_activity["pending_wake_reapply"] = True
                                            log.info(
                                                "Rato: enviado via 2.4G sem atividade recente; "
                                                "será confirmado ao mexer no rato."
                                            )
                                        else:
                                            mouse_activity["pending_wake_reapply"] = False
                                else:
                                    if not warned_offline[dev_name]:
                                        log.warning(
                                            "%s: falha ao aplicar '%s' (%s)",
                                            "Teclado" if dev_name == "keyboard" else "Rato",
                                            desired,
                                            info,
                                        )
                                        warned_offline[dev_name] = True

                still_pending = any(
                    cfg.get(f"control_{dev_name}", True) and last_applied[dev_name] != desired
                    for dev_name in ("keyboard", "mouse")
                )

        if still_pending:
            wait_sec = backoff_schedule[min(retry_step, len(backoff_schedule) - 1)]
            retry_step += 1
        else:
            retry_step = 0
            wait_sec = 30.0

        wake_event.wait(timeout=wait_sec)
        wake_event.clear()


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        raise SystemExit(run_daemon())
    sr, ss = get_sun_times()
    print(f"Maia, PT -> Nascer: {sr.strftime('%H:%M')}, Pôr do sol: {ss.strftime('%H:%M')}")
