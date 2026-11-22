#!/usr/bin/env python3
"""
Pad2Xbox - simple GUI: calibrate a generic DirectInput gamepad and run it
as a virtual Xbox 360 (XInput) controller through ViGEmBus.

Needs: ViGEmBus driver, and  pip install pygame vgamepad
Build one exe with build_exe.bat (PyInstaller).
"""

import os
import sys
import json
import math
import time
import queue
import threading
import subprocess

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import tkinter as tk
from tkinter import ttk, messagebox
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False

try:
    import pygame
except ImportError:
    pygame = None

APP_NAME = "Pad2Xbox"
TASK_NAME = "Pad2Xbox"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
MAP_PATH = os.path.join(DATA_DIR, "pad2xbox_map.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SKIP_WORDS = ("xbox", "xinput", "vigem", "virtual")

BUTTON_STEPS = [
    ("A", "Cross (X) - bottom face button"),
    ("B", "Circle - right face button"),
    ("X", "Square - left face button"),
    ("Y", "Triangle - top face button"),
    ("LB", "L1"),
    ("RB", "R1"),
    ("BACK", "Share / Select"),
    ("START", "Options / Start"),
    ("LS", "L3 - click the LEFT stick in"),
    ("RS", "R3 - click the RIGHT stick in"),
    ("GUIDE", "PS / Home button"),
]
STICK_STEPS = [
    ("LX", "LEFT stick: push fully to the RIGHT"),
    ("LY", "LEFT stick: push fully UP"),
    ("RX", "RIGHT stick: push fully to the RIGHT"),
    ("RY", "RIGHT stick: push fully UP"),
]
TRIGGER_STEPS = [
    ("LT", "L2 trigger - press and hold"),
    ("RT", "R2 trigger - press and hold"),
]
DPAD_STEPS = [
    ("UP", "D-pad UP"),
    ("DOWN", "D-pad DOWN"),
    ("LEFT", "D-pad LEFT"),
    ("RIGHT", "D-pad RIGHT"),
]


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def axis_value(axes, entry):
    if not entry:
        return 0.0
    idx = entry["index"]
    if idx >= len(axes):
        return 0.0
    return clamp(axes[idx] * entry.get("scale", 1), -1.0, 1.0)


def stick_value(axes, cfg_axes, kx, ky, deadzone=0.08, curve=1.0, anti=0.0):
    x = axis_value(axes, cfg_axes.get(kx))
    y = axis_value(axes, cfg_axes.get(ky))
    mag = math.hypot(x, y)
    if mag <= deadzone:
        return 0.0, 0.0
    scaled = (min(mag, 1.0) - deadzone) / (1.0 - deadzone)
    if curve and curve != 1.0:
        scaled = scaled ** curve
    if anti:
        scaled = min(1.0, scaled + anti * (1.0 - scaled))
    k = scaled / mag
    return clamp(x * k, -1.0, 1.0), clamp(y * k, -1.0, 1.0)


def trigger_value(axes, btns, entry, threshold=0):
    if not entry:
        return 0
    i = entry["index"]
    if entry["kind"] == "button":
        return 255 if i < len(btns) and btns[i] else 0
    if i >= len(axes):
        return 0
    span = entry["full"] - entry["rest"]
    if abs(span) < 1e-6:
        return 0
    t = clamp((axes[i] - entry["rest"]) / span, 0.0, 1.0)
    if t < 0.03:
        t = 0.0
    val = int(round(t * 255))
    if val < threshold:
        val = 0
    return val


def dpad_pressed(btns, hat, entry):
    if not entry:
        return False
    if entry["kind"] == "button":
        i = entry["index"]
        return i < len(btns) and bool(btns[i])
    vx, vy = entry["value"]
    return (vx != 0 and hat[0] == vx) or (vy != 0 and hat[1] == vy)


DPAD_DEFAULTS = {"UP": [0, 1], "DOWN": [0, -1], "LEFT": [-1, 0], "RIGHT": [1, 0]}

HIDHIDE_CLI = r"C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe"


def hidhide_available():
    return os.path.isfile(HIDHIDE_CLI)


def hidhide_app_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(pyw):
            exe = pyw
    return exe


def hidhide_list_gaming():
    if not hidhide_available():
        return []
    try:
        out = subprocess.check_output([HIDHIDE_CLI, "--dev-gaming"],
                                     creationflags=NO_WINDOW, text=True,
                                     stderr=subprocess.DEVNULL)
    except Exception:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    res = []
    for group in data:
        for dev in group.get("devices", []):
            if dev.get("gamingDevice"):
                res.append(dev)
    return res


def hidhide_find_instance(pad_name):
    low = (pad_name or "").lower()
    devs = hidhide_list_gaming()
    for dev in devs:
        fn = (dev.get("friendlyName") or dev.get("product") or "").lower()
        if low and low in fn and not any(w in fn for w in SKIP_WORDS):
            return dev.get("deviceInstancePath")
    for dev in devs:
        fn = (dev.get("friendlyName") or "").lower()
        if not any(w in fn for w in SKIP_WORDS):
            return dev.get("deviceInstancePath")
    return None


def get_foreground_process_name():
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value)
        if not h:
            return None
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.psapi.GetModuleBaseNameW(h, None, buf, 260)
        ctypes.windll.kernel32.CloseHandle(h)
        return (buf.value or "").lower()
    except Exception:
        return None


def hidhide_hidden_count():
    if not hidhide_available():
        return 0
    try:
        out = subprocess.check_output([HIDHIDE_CLI, "--dev-list"],
                                     creationflags=NO_WINDOW, text=True,
                                     stderr=subprocess.DEVNULL)
    except Exception:
        return 0
    try:
        data = json.loads(out)
    except Exception:
        return 0
    # dev-list may be a list of device objects or groups
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return len(data.get("devices", []))
    return 0


def apply_dpad_defaults(cfg):
    dp = cfg.setdefault("dpad", {})
    for key, val in DPAD_DEFAULTS.items():
        if key not in dp:
            dp[key] = {"kind": "hat", "value": list(val)}
    return cfg


def match_pad(name, idx, name_filter):
    low = name.lower()
    if name_filter:
        return name_filter.lower() in low
    return not any(w in low for w in SKIP_WORDS)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ----------------------------------------------------------------------------
# Worker engine (all pygame work happens inside one worker thread)
# ----------------------------------------------------------------------------

class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, post):
        self.post = post
        self.thread = None
        self.stop = threading.Event()
        self.skip = threading.Event()
        self.mode = "idle"

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, mode, func, *args):
        if self.busy():
            return False
        self.stop.clear()
        self.skip.clear()
        self.mode = mode
        self.post("mode", mode)

        def runner():
            try:
                pygame.init()
                pygame.joystick.init()
                func(*args)
            except Cancelled:
                self.post("log", "Cancelled.")
            except Exception as exc:
                self.post("log", "Error: %s" % exc)
            finally:
                try:
                    pygame.quit()
                except Exception:
                    pass
                self.mode = "idle"
                self.post("prompt", "")
                self.post("status", "Stopped", "gray")
                self.post("pad", "")
                self.post("mode", "idle")

        self.thread = threading.Thread(target=runner, daemon=True)
        self.thread.start()
        return True

    def request_stop(self):
        self.stop.set()

    def check(self):
        if self.stop.is_set():
            raise Cancelled()

    # ---- pad access ------------------------------------------------------
    def acquire_pad(self, name_filter):
        pygame.joystick.quit()
        pygame.joystick.init()
        chosen = None
        for i in range(pygame.joystick.get_count()):
            js = pygame.joystick.Joystick(i)
            if chosen is None and match_pad(js.get_name(), i, name_filter):
                chosen = js
            else:
                js.quit()
        return chosen

    def acquire_all_pads(self, name_filter, max_n):
        pygame.joystick.quit()
        pygame.joystick.init()
        result = []
        for i in range(pygame.joystick.get_count()):
            if len(result) >= max_n:
                break
            js = pygame.joystick.Joystick(i)
            if match_pad(js.get_name(), i, name_filter):
                result.append(js)
            else:
                js.quit()
        return result

    @staticmethod
    def read_state(js):
        axes = [js.get_axis(i) for i in range(js.get_numaxes())]
        btns = [js.get_button(i) for i in range(js.get_numbuttons())]
        hat = js.get_hat(0) if js.get_numhats() > 0 else (0, 0)
        return axes, btns, hat

    def snapshot(self, js):
        pygame.event.pump()
        return self.read_state(js)

    # ---- calibration -----------------------------------------------------
    def wait_neutral(self, js, base_axes, timeout=4.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check()
            axes, btns, hat = self.snapshot(js)
            calm = all(abs(a - b) < 0.25 for a, b in zip(axes, base_axes))
            if calm and not any(btns) and hat == (0, 0):
                time.sleep(0.2)
                return
            time.sleep(0.02)

    def wait_input(self, js, base_axes, want, used_b, used_a, timeout=20.0):
        self.skip.clear()
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check()
            if self.skip.is_set():
                self.skip.clear()
                return None
            axes, btns, hat = self.snapshot(js)
            if "button" in want:
                for i, v in enumerate(btns):
                    if v and i not in used_b:
                        return ("button", i)
            if "hat" in want and hat != (0, 0):
                return ("hat", hat)
            if "axis" in want:
                best, best_dev = None, 0.0
                for i, (a, b) in enumerate(zip(axes, base_axes)):
                    if i in used_a:
                        continue
                    dev = abs(a - b)
                    if dev > best_dev:
                        best, best_dev = i, dev
                if best is not None and best_dev > 0.6:
                    return ("axis", best)
            time.sleep(0.01)
        return None

    def measure_peak(self, js, idx, base):
        peak = base
        t0 = time.time()
        while time.time() - t0 < 0.6:
            self.check()
            axes, _, _ = self.snapshot(js)
            if abs(axes[idx] - base) > abs(peak - base):
                peak = axes[idx]
            time.sleep(0.01)
        return peak

    def calibrate(self, name_filter):
        self.post("status", "Calibrating", "orange")
        js = self.acquire_pad(name_filter)
        if js is None:
            self.post("log", "No physical gamepad found. Plug it in and try again.")
            return
        self.post("pad", js.get_name())
        self.post("log", "Pad: %s (axes=%d buttons=%d hats=%d)" % (
            js.get_name(), js.get_numaxes(), js.get_numbuttons(), js.get_numhats()))
        self.post("prompt", "Keep your hands off the pad...")
        time.sleep(1.2)
        base = self.snapshot(js)[0]

        cfg = {"version": 1, "pad_name": js.get_name(),
               "buttons": {}, "axes": {}, "triggers": {}, "dpad": {}}
        used_b, used_a = set(), set()
        total = (len(BUTTON_STEPS) + len(STICK_STEPS)
                 + len(TRIGGER_STEPS) + len(DPAD_STEPS))
        done = 0

        def step(label):
            self.post("prompt", label)
            self.post("progress", done, total)

        for key, label in BUTTON_STEPS:
            step("Press: %s   [%s]" % (key, label))
            res = self.wait_input(js, base, {"button"}, used_b, used_a)
            done += 1
            if res is None:
                self.post("log", "%s skipped" % key)
                continue
            cfg["buttons"][key] = res[1]
            used_b.add(res[1])
            self.post("log", "%s -> button %d" % (key, res[1]))
            self.wait_neutral(js, base)

        for key, label in STICK_STEPS:
            step("%s   [%s]" % (key, label))
            res = self.wait_input(js, base, {"axis"}, used_b, used_a)
            done += 1
            if res is None:
                self.post("log", "%s skipped" % key)
                continue
            idx = res[1]
            peak = self.measure_peak(js, idx, base[idx])
            scale = 1 if peak - base[idx] > 0 else -1
            cfg["axes"][key] = {"index": idx, "scale": scale}
            used_a.add(idx)
            self.post("log", "%s -> axis %d (scale %+d)" % (key, idx, scale))
            self.wait_neutral(js, base)

        for key, label in TRIGGER_STEPS:
            step("%s   [%s]" % (key, label))
            res = self.wait_input(js, base, {"button", "axis"}, used_b, used_a)
            done += 1
            if res is None:
                self.post("log", "%s skipped" % key)
                continue
            if res[0] == "button":
                cfg["triggers"][key] = {"kind": "button", "index": res[1]}
                used_b.add(res[1])
                self.post("log", "%s -> digital button %d" % (key, res[1]))
            else:
                idx = res[1]
                full = self.measure_peak(js, idx, base[idx])
                cfg["triggers"][key] = {"kind": "axis", "index": idx,
                                        "rest": base[idx], "full": full}
                used_a.add(idx)
                self.post("log", "%s -> analog axis %d" % (key, idx))
            self.wait_neutral(js, base)

        for key, label in DPAD_STEPS:
            step("Press: %s" % label)
            res = self.wait_input(js, base, {"hat", "button"}, used_b, used_a)
            done += 1
            if res is None:
                self.post("log", "%s skipped" % key)
                continue
            if res[0] == "hat":
                cfg["dpad"][key] = {"kind": "hat", "value": list(res[1])}
                self.post("log", "%s -> hat %s" % (key, (res[1],)))
            else:
                cfg["dpad"][key] = {"kind": "button", "index": res[1]}
                used_b.add(res[1])
                self.post("log", "%s -> button %d" % (key, res[1]))
            self.wait_neutral(js, base)

        if js.get_numhats() > 0:
            before = len(cfg["dpad"])
            apply_dpad_defaults(cfg)
            if len(cfg["dpad"]) != before:
                self.post("log", "D-pad: using the standard hat for skipped directions.")
        save_json(MAP_PATH, cfg)
        self.post("progress", total, total)
        self.post("log", "Calibration saved. You can start the bridge now.")
        self.post("calibrated", "")

    # ---- raw live view ---------------------------------------------------
    def raw_view(self, name_filter):
        self.post("status", "Live test", "orange")
        js = self.acquire_pad(name_filter)
        if js is None:
            self.post("log", "No physical gamepad found.")
            return
        self.post("pad", js.get_name())
        self.post("log", "Live view of: %s (Stop to end)" % js.get_name())
        try:
            while not self.stop.is_set():
                axes, btns, hat = self.snapshot(js)
                on = [i for i, v in enumerate(btns) if v]
                self.post("raw", "axes=%s  btns=%s  hat=%s" % (
                    [round(a, 2) for a in axes], on, hat))
                time.sleep(0.05)
        finally:
            try:
                js.quit()
            except Exception:
                pass
            self.post("raw", "")

    # ---- bridge ----------------------------------------------------------
    def bridge(self, cfg, settings, name_filter, hz=250):
        try:
            import vgamepad as vg
        except ImportError:
            self.post("log", "Missing module vgamepad.")
            return
        output_mode = (settings or {}).get("output_mode", "xbox360")
        ds4_mode = (output_mode == "ds4")
        try:
            if ds4_mode:
                pad = vg.VDS4Gamepad()
            else:
                pad = vg.VX360Gamepad()
        except Exception as exc:
            self.post("log", "Cannot create virtual pad. Is ViGEmBus installed?")
            self.post("log", "Details: %s" % exc)
            return

        if ds4_mode:
            B = vg.DS4_BUTTONS
            xb = {
                "A": B.DS4_BUTTON_CROSS, "B": B.DS4_BUTTON_CIRCLE,
                "X": B.DS4_BUTTON_SQUARE, "Y": B.DS4_BUTTON_TRIANGLE,
                "LB": B.DS4_BUTTON_SHOULDER_LEFT, "RB": B.DS4_BUTTON_SHOULDER_RIGHT,
                "BACK": B.DS4_BUTTON_SHARE, "START": B.DS4_BUTTON_OPTIONS,
                "LS": B.DS4_BUTTON_THUMB_LEFT, "RS": B.DS4_BUTTON_THUMB_RIGHT,
            }
        else:
            B = vg.XUSB_BUTTON
            xb = {
                "A": B.XUSB_GAMEPAD_A, "B": B.XUSB_GAMEPAD_B,
                "X": B.XUSB_GAMEPAD_X, "Y": B.XUSB_GAMEPAD_Y,
                "LB": B.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
                "BACK": B.XUSB_GAMEPAD_BACK, "START": B.XUSB_GAMEPAD_START,
                "LS": B.XUSB_GAMEPAD_LEFT_THUMB, "RS": B.XUSB_GAMEPAD_RIGHT_THUMB,
                "GUIDE": B.XUSB_GAMEPAD_GUIDE,
                "UP": B.XUSB_GAMEPAD_DPAD_UP, "DOWN": B.XUSB_GAMEPAD_DPAD_DOWN,
                "LEFT": B.XUSB_GAMEPAD_DPAD_LEFT, "RIGHT": B.XUSB_GAMEPAD_DPAD_RIGHT,
            }
        apply_dpad_defaults(cfg)
        period = 1.0 / max(30, hz)
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


        mode_name = "DualShock 4" if ds4_mode else "Xbox 360"
        max_pads = max(1, min(4, int((settings or {}).get("max_pads", 1))))
        self.post("log", "Virtual %s pad(s) up to %d." % (mode_name, max_pads))
        self.post("status", "Running (%s active)" % mode_name, "green")

        def make_pad():
            if ds4_mode:
                return vg.VDS4Gamepad()
            return vg.VX360Gamepad()

        def setup_pairs():
            pairs = []
            pads_list = self.acquire_all_pads(name_filter, max_pads)
            for js in pads_list:
                try:
                    pad = make_pad()
                except Exception as exc:
                    self.post("log", "Cannot create virtual pad: %s" % exc)
                    try:
                        js.quit()
                    except Exception:
                        pass
                    continue
                pairs.append({"js": js, "pad": pad, "last": None})
                self.post("log", "Connected: %s" % js.get_name())
            if pairs:
                self.post("pad", ", ".join(p["js"].get_name() for p in pairs))
            else:
                self.post("pad", "")
            return pairs, pygame.joystick.get_count()

        def teardown_pairs(pairs):
            for p in pairs:
                try:
                    p["pad"].reset()
                    p["pad"].update()
                except Exception:
                    pass
                try:
                    p["js"].quit()
                except Exception:
                    pass

        def update_pair(p):
            js = p["js"]
            pad = p["pad"]
            axes, btns, hat = self.read_state(js)
            pressed = set()
            for key, idx in cfg["buttons"].items():
                if idx < len(btns) and btns[idx]:
                    pressed.add(key)
            for key, entry in cfg["dpad"].items():
                if dpad_pressed(btns, hat, entry):
                    pressed.add(key)
            remap = (settings or {}).get("button_remap", {})
            if isinstance(remap, dict) and remap:
                pressed = set(remap.get(b, b) for b in pressed)
            turbo = (settings or {}).get("turbo_buttons", [])
            if turbo:
                nowt = time.time()
                for b in turbo:
                    if b in pressed and (int(nowt * 10) % 2 == 1):
                        pressed.discard(b)

            combo_map = (settings or {}).get("combo_map", {})
            if isinstance(combo_map, dict):
                for combo, target in combo_map.items():
                    parts = [p.strip() for p in str(combo).split("+")]
                    if parts and all(p in pressed for p in parts):
                        pressed.add(str(target))

            dz_l = (settings or {}).get("deadzone_l", (settings or {}).get("deadzone", 0.08))
            dz_r = (settings or {}).get("deadzone_r", (settings or {}).get("deadzone", 0.08))
            curve = (settings or {}).get("curve", 1.0)
            anti = (settings or {}).get("anti_deadzone", 0.0)
            trig_thr = (settings or {}).get("trigger_threshold", 0)
            lx, ly = stick_value(axes, cfg["axes"], "LX", "LY", dz_l, curve, anti)
            rx, ry = stick_value(axes, cfg["axes"], "RX", "RY", dz_r, curve, anti)
            lt = trigger_value(axes, btns, cfg["triggers"].get("LT"), trig_thr)
            rt = trigger_value(axes, btns, cfg["triggers"].get("RT"), trig_thr)
            state = (frozenset(pressed), round(lx, 3), round(ly, 3),
                     round(rx, 3), round(ry, 3), lt, rt)
            if state != p["last"]:
                p["last"] = state
                if ds4_mode:
                    up = "UP" in pressed
                    down = "DOWN" in pressed
                    left = "LEFT" in pressed
                    right = "RIGHT" in pressed
                    if up and left:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTHWEST)
                    elif up and right:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTHEAST)
                    elif down and left:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTHWEST)
                    elif down and right:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTHEAST)
                    elif up:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTH)
                    elif down:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTH)
                    elif left:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_WEST)
                    elif right:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_EAST)
                    else:
                        pad.directional_pad(vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NONE)
                    for key, const in xb.items():
                        if key in ("UP", "DOWN", "LEFT", "RIGHT"):
                            continue
                        if const is None:
                            continue
                        if key in pressed:
                            pad.press_button(button=const)
                        else:
                            pad.release_button(button=const)
                else:
                    for key, const in xb.items():
                        if key in pressed:
                            pad.press_button(button=const)
                        else:
                            pad.release_button(button=const)
                pad.left_joystick_float(x_value_float=lx, y_value_float=ly)
                pad.right_joystick_float(x_value_float=rx, y_value_float=ry)
                pad.left_trigger(value=lt)
                pad.right_trigger(value=rt)
                pad.update()

        pairs = []
        known_count = 0
        last_scan = 0.0
        waiting_shown = False
        last_fg_check = 0.0
        current_profile = None
        try:
            while not self.stop.is_set():
                t_start = time.time()
                # auto-switch profile
                if (settings or {}).get("auto_profile"):
                    if t_start - last_fg_check >= 2.0:
                        last_fg_check = t_start
                        pname = get_foreground_process_name()
                        target = (settings or {}).get("profile_map", {}).get(pname or "")
                        if target and target != current_profile:
                            if target == "default":
                                new_cfg = load_json(MAP_PATH, None)
                                new_settings = load_json(SETTINGS_PATH, None)
                            else:
                                data = load_json(os.path.join(PROFILES_DIR, target + ".json"), None)
                                new_cfg = data.get("map") if data else None
                                new_settings = data.get("settings") if data else None
                            if new_cfg:
                                cfg = new_cfg
                                if new_settings:
                                    settings = new_settings
                                current_profile = target
                                self.post("log", "Auto-switched to profile: %s (%s)" % (target, pname))
                if not pairs:
                    self.post("status", "Waiting for gamepad", "orange")
                    if t_start - last_scan >= 1.0:
                        last_scan = t_start
                        pairs, known_count = setup_pairs()
                        if pairs:
                            waiting_shown = False
                            self.post("status", "Running (%s active)" % mode_name, "green")
                        elif not waiting_shown:
                            self.post("log", "Waiting for gamepad...")
                            waiting_shown = True
                    time.sleep(0.05)
                    continue

                pygame.event.pump()
                pygame.event.clear()
                rescan = False
                if pygame.joystick.get_count() != known_count:
                    rescan = True
                if not rescan:
                    for p in list(pairs):
                        try:
                            update_pair(p)
                        except pygame.error:
                            rescan = True
                            break
                        except Exception as exc:
                            self.post("log", "Pad error: %s" % exc)
                            rescan = True
                            break
                if rescan:
                    self.post("log", "Device list changed, rescanning...")
                    teardown_pairs(pairs)
                    pairs = []
                    last_scan = 0.0
                    continue

                spent = time.time() - t_start
                if spent < period:
                    time.sleep(period - spent)
        finally:
            teardown_pairs(pairs)
            self.post("log", "Bridge stopped.")


# ----------------------------------------------------------------------------
# Startup task helpers
# ----------------------------------------------------------------------------

def startup_command():
    if getattr(sys, "frozen", False):
        return '"%s" --autostart' % sys.executable
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(pyw):
            exe = pyw
    return '"%s" "%s" --autostart' % (exe, os.path.abspath(__file__))


def startup_enabled():
    if os.name != "nt":
        return False
    rc = subprocess.call(["schtasks", "/Query", "/TN", TASK_NAME],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    return rc == 0


def set_startup(enable):
    if os.name != "nt":
        return False
    if enable:
        cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON",
               "/TR", startup_command(), "/F"]
    else:
        cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW) == 0


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

class App:
    COLORS = {"green": "#1a9a3a", "orange": "#d98200", "gray": "#666666", "red": "#c62828"}

    def __init__(self, root, autostart):
        self.root = root
        self.q = queue.Queue()
        self.engine = Engine(lambda *m: self.q.put(m))
        self.settings = load_json(SETTINGS_PATH, {"deadzone": 0.08, "name": ""})

        root.title("Pad2Xbox - Gamepad to Xbox 360")
        root.geometry("560x520")
        root.minsize(520, 480)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)

        self.status = tk.Label(main, text="Stopped", fg=self.COLORS["gray"],
                               font=("Segoe UI", 16, "bold"))
        self.status.pack(anchor="w")
        self.pad_lbl = tk.Label(main, text="", fg="#444444", font=("Segoe UI", 10))
        self.pad_lbl.pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(main)
        row.pack(fill="x", pady=4)
        self.btn_start = ttk.Button(row, text="Start bridge", command=self.start_bridge)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ttk.Button(row, text="Stop", command=self.stop_all, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 6))
        self.btn_cal = ttk.Button(row, text="Calibrate", command=self.start_calibration)
        self.btn_cal.pack(side="left")

        self.prompt = tk.Label(main, text="", font=("Segoe UI", 13, "bold"),
                               fg="#0b4f9c", wraplength=520, justify="left")
        self.prompt.pack(anchor="w", pady=(10, 2))
        pr = ttk.Frame(main)
        pr.pack(fill="x")
        self.progress = ttk.Progressbar(pr, maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.btn_skip = ttk.Button(pr, text="Skip", command=self.engine.skip.set, state="disabled")
        self.btn_skip.pack(side="left", padx=(6, 0))

        dz = ttk.Frame(main)
        dz.pack(fill="x", pady=(12, 0))
        ttk.Label(dz, text="Stick deadzone:").pack(side="left")
        self.dz_var = tk.DoubleVar(value=float(self.settings.get("deadzone", 0.08)))
        self.dz_val = ttk.Label(dz, text="%.2f" % self.dz_var.get(), width=5)
        ttk.Scale(dz, from_=0.0, to=0.3, variable=self.dz_var,
                  command=self.on_dz).pack(side="left", fill="x", expand=True, padx=8)
        self.dz_val.pack(side="left")

        self.startup_var = tk.BooleanVar(value=startup_enabled())
        ttk.Checkbutton(main, text="Start automatically when I log in to Windows",
                        variable=self.startup_var,
                        command=self.on_startup).pack(anchor="w", pady=(8, 0))

        self.hide_var = tk.BooleanVar(value=bool(self.settings.get("hide_original", False)))
        self.hide_cb = ttk.Checkbutton(main, text="Hide original gamepad from other apps (HidHide)",
                                      variable=self.hide_var,
                                      command=self.on_hide_toggle)
        self.hide_cb.pack(anchor="w", pady=(4, 0))
        self.hide_status = tk.Label(main, text="", fg="#444444", font=("Segoe UI", 9))
        self.hide_status.pack(anchor="w")

        # ---- stick / trigger tuning (suggestion 5) + output mode (suggestion 9) ----
        tune = ttk.LabelFrame(main, text="Stick & trigger tuning", padding=6)
        tune.pack(fill="x", pady=(6, 0))

        self.dz_l_var = tk.DoubleVar(value=float(self.settings.get("deadzone_l", self.settings.get("deadzone", 0.08))))
        self.dz_r_var = tk.DoubleVar(value=float(self.settings.get("deadzone_r", self.settings.get("deadzone", 0.08))))
        for label, var in (("L deadzone", self.dz_l_var), ("R deadzone", self.dz_r_var)):
            r = ttk.Frame(tune); r.pack(fill="x", pady=2)
            ttk.Label(r, text=label, width=12).pack(side="left")
            ttk.Scale(r, from_=0.0, to=0.3, variable=var).pack(side="left", fill="x", expand=True, padx=6)

        r = ttk.Frame(tune); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Curve", width=12).pack(side="left")
        self.curve_var = tk.StringVar(value=str(self.settings.get("curve", 1.0)))
        ttk.Combobox(r, textvariable=self.curve_var, values=["1.0", "1.5", "2.0"], width=8).pack(side="left")

        r = ttk.Frame(tune); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Anti-deadzone", width=12).pack(side="left")
        self.anti_var = tk.DoubleVar(value=float(self.settings.get("anti_deadzone", 0.0)))
        ttk.Scale(r, from_=0.0, to=0.15, variable=self.anti_var).pack(side="left", fill="x", expand=True, padx=6)

        r = ttk.Frame(tune); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Trigger thr.", width=12).pack(side="left")
        self.trigthr_var = tk.IntVar(value=int(self.settings.get("trigger_threshold", 0)))
        ttk.Scale(r, from_=0, to=60, variable=self.trigthr_var).pack(side="left", fill="x", expand=True, padx=6)

        r = ttk.Frame(tune); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Output", width=12).pack(side="left")
        self.output_var = tk.StringVar(value=self.settings.get("output_mode", "xbox360"))
        ttk.Combobox(r, textvariable=self.output_var, values=["xbox360", "ds4"], width=12).pack(side="left")

        r = ttk.Frame(tune); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Max pads", width=12).pack(side="left")
        self.maxpads_var = tk.IntVar(value=int(self.settings.get("max_pads", 1)))
        ttk.Spinbox(r, from_=1, to=4, textvariable=self.maxpads_var, width=6).pack(side="left")

        # ---- profiles (suggestion 6) ----
        prof = ttk.LabelFrame(main, text="Profiles", padding=6)
        prof.pack(fill="x", pady=(6, 0))
        self.profile_var = tk.StringVar(value="default")
        self.profile_cb = ttk.Combobox(prof, textvariable=self.profile_var, values=["default"], width=18)
        self.profile_cb.pack(side="left")
        ttk.Button(prof, text="Refresh", command=self.refresh_profiles).pack(side="left", padx=4)
        ttk.Button(prof, text="Save", command=self.save_profile).pack(side="left", padx=4)
        ttk.Button(prof, text="Load", command=self.load_profile).pack(side="left", padx=4)

        # ---- auto-switch by foreground app (suggestion 6) ----
        self.autosw_var = tk.BooleanVar(value=bool(self.settings.get("auto_profile", False)))
        ttk.Checkbutton(main, text="Auto-switch profile by foreground app",
                        variable=self.autosw_var).pack(anchor="w", pady=(4, 0))
        r = ttk.Frame(main); r.pack(fill="x")
        ttk.Label(r, text="App->Profile JSON", width=16).pack(side="left")
        self.appmap_var = tk.StringVar(value=json.dumps(self.settings.get("profile_map", {})))
        ttk.Entry(r, textvariable=self.appmap_var).pack(side="left", fill="x", expand=True)

        # ---- remap (suggestion 7) ----
        r = ttk.Frame(main); r.pack(fill="x", pady=(6, 0))
        ttk.Label(r, text="Remap JSON", width=12).pack(side="left")
        self.remap_var = tk.StringVar(value=json.dumps(self.settings.get("button_remap", {})))
        ttk.Entry(r, textvariable=self.remap_var).pack(side="left", fill="x", expand=True)

        r = ttk.Frame(main); r.pack(fill="x")
        ttk.Label(r, text="Turbo (list)", width=12).pack(side="left")
        self.turbo_var = tk.StringVar(value=json.dumps(self.settings.get("turbo_buttons", [])))
        ttk.Entry(r, textvariable=self.turbo_var).pack(side="left", fill="x", expand=True)

        r = ttk.Frame(main); r.pack(fill="x")
        ttk.Label(r, text="Combo JSON", width=12).pack(side="left")
        self.combo_var = tk.StringVar(value=json.dumps(self.settings.get("combo_map", {})))
        ttk.Entry(r, textvariable=self.combo_var).pack(side="left", fill="x", expand=True)

        raw_row = ttk.Frame(main)
        raw_row.pack(fill="x", pady=(6, 0))
        self.btn_raw = ttk.Button(raw_row, text="Live test", command=self.start_raw)
        self.btn_raw.pack(side="left")
        ttk.Button(raw_row, text="Export diag", command=self.export_diagnostics).pack(side="left", padx=(6, 0))
        self.raw_lbl = tk.Label(raw_row, text="", font=("Consolas", 9), anchor="w")
        self.raw_lbl.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.log = tk.Text(main, height=10, state="disabled", font=("Consolas", 9),
                           bg="#f6f6f6", relief="flat")
        self.log.pack(fill="both", expand=True, pady=(10, 0))

        if pygame is None:
            self.write("Missing module pygame. Install it: pip install pygame vgamepad")
            self.btn_start.config(state="disabled")
            self.btn_cal.config(state="disabled")
        elif not os.path.isfile(MAP_PATH):
            self.write("First time: click Calibrate and follow the prompts.")

        root.after(100, self.pump)
        if self.hide_var.get() and hidhide_available():
            root.after(500, self.on_hide_toggle)
        if autostart and pygame is not None and os.path.isfile(MAP_PATH):
            root.after(300, self.start_bridge)

    # ---- UI helpers --------------------------------------------------------
    def write(self, text):
        line = time.strftime("%H:%M:%S ") + text
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, "pad2xbox.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
        except Exception:
            pass

    def on_dz(self, _v):
        self.dz_val.config(text="%.2f" % self.dz_var.get())

    def save_settings(self):
        self.settings["deadzone"] = round(self.dz_var.get(), 3)
        self.settings["deadzone_l"] = round(self.dz_l_var.get(), 3)
        self.settings["deadzone_r"] = round(self.dz_r_var.get(), 3)
        try:
            self.settings["curve"] = float(self.curve_var.get())
        except Exception:
            self.settings["curve"] = 1.0
        self.settings["anti_deadzone"] = round(self.anti_var.get(), 3)
        self.settings["trigger_threshold"] = int(self.trigthr_var.get())
        self.settings["output_mode"] = self.output_var.get()
        self.settings["hide_original"] = bool(self.hide_var.get())
        self.settings["max_pads"] = int(self.maxpads_var.get())
        try:
            self.settings["button_remap"] = json.loads(self.remap_var.get() or "{}")
        except Exception:
            self.settings["button_remap"] = {}
        try:
            self.settings["turbo_buttons"] = json.loads(self.turbo_var.get() or "[]")
        except Exception:
            self.settings["turbo_buttons"] = []
        try:
            self.settings["combo_map"] = json.loads(self.combo_var.get() or "{}")
        except Exception:
            self.settings["combo_map"] = {}
        self.settings["auto_profile"] = bool(self.autosw_var.get())
        try:
            self.settings["profile_map"] = json.loads(self.appmap_var.get() or "{}")
        except Exception:
            self.settings["profile_map"] = {}
        try:
            save_json(SETTINGS_PATH, self.settings)
        except Exception:
            pass

    def on_startup(self):
        want = self.startup_var.get()
        if not set_startup(want):
            self.startup_var.set(not want)
            messagebox.showerror(APP_NAME, "Could not change the startup task.")
        else:
            self.write("Auto-start %s." % ("enabled" if want else "disabled"))

    def on_hide_toggle(self):
        want = self.hide_var.get()
        self.settings["hide_original"] = bool(want)
        self.save_settings()
        if not hidhide_available():
            self.hide_var.set(False)
            self.settings["hide_original"] = False
            self.save_settings()
            messagebox.showerror(APP_NAME, "HidHide is not installed.")
            return

        def worker():
            cfg = load_json(MAP_PATH, {}) or {}
            pad_name = cfg.get("pad_name") or self.settings.get("name") or ""
            app = hidhide_app_path()
            if want:
                inst = hidhide_find_instance(pad_name)
                if not inst:
                    self.q.put(("log", "HidHide: original gamepad not found."))
                    self.q.put(("hide_status", "Not found"))
                    self.hide_var.set(False)
                    self.settings["hide_original"] = False
                    self.save_settings()
                    return
                subprocess.run([HIDHIDE_CLI, "--app-reg", app],
                               creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                subprocess.run([HIDHIDE_CLI, "--dev-hide", inst],
                               creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                subprocess.run([HIDHIDE_CLI, "--cloak-on"],
                               creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                self.q.put(("log", "HidHide: original pad hidden from other apps."))
                self.q.put(("hide_status", "Hidden"))
            else:
                inst = hidhide_find_instance(pad_name)
                if inst:
                    subprocess.run([HIDHIDE_CLI, "--dev-unhide", inst],
                                   creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                subprocess.run([HIDHIDE_CLI, "--app-unreg", app],
                               creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                if hidhide_hidden_count() == 0:
                    subprocess.run([HIDHIDE_CLI, "--cloak-off"],
                                   creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                self.q.put(("log", "HidHide: original pad visible again."))
                self.q.put(("hide_status", "Visible"))
        threading.Thread(target=worker, daemon=True).start()

    def export_diagnostics(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            out = os.path.join(DATA_DIR, "diagnostics.txt")
            lines = []
            lines.append("Pad2Xbox diagnostics")
            lines.append("Time: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
            lines.append("Python: %s" % sys.version)
            lines.append("Frozen: %s" % getattr(sys, "frozen", False))
            lines.append("Exe: %s" % sys.executable)
            lines.append("ViGEmBus: %s" % os.path.exists(r"C:\Windows\System32\drivers\ViGEmBus.sys"))
            lines.append("HidHide: %s" % hidhide_available())
            lines.append("Map path: %s (exists=%s)" % (MAP_PATH, os.path.isfile(MAP_PATH)))
            try:
                lines.append("Map: %s" % json.dumps(load_json(MAP_PATH, {}), indent=2))
            except Exception as e:
                lines.append("Map error: %s" % e)
            lines.append("Settings: %s" % json.dumps(self.settings, indent=2))
            # gaming devices
            if hidhide_available():
                try:
                    devs = hidhide_list_gaming()
                    lines.append("Gaming devices: %s" % json.dumps(devs, indent=2))
                except Exception as e:
                    lines.append("Gaming devices error: %s" % e)
            # log tail
            logp = os.path.join(DATA_DIR, "pad2xbox.log")
            if os.path.isfile(logp):
                with open(logp, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-50:]
                lines.append("Log tail:")
                lines.extend(tail)
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            messagebox.showinfo(APP_NAME, "Diagnostics saved to:\n" + out)
            self.write("Diagnostics exported.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Export failed: %s" % exc)

    def refresh_profiles(self):
        os.makedirs(PROFILES_DIR, exist_ok=True)
        vals = ["default"] + sorted(f for f in os.listdir(PROFILES_DIR) if f.endswith(".json"))
        vals = [v.replace(".json", "") for v in vals]
        self.profile_cb["values"] = vals
        if self.profile_var.get() not in vals:
            self.profile_var.set("default")

    def save_profile(self):
        os.makedirs(PROFILES_DIR, exist_ok=True)
        name = self.profile_var.get().strip() or "default"
        if name == "default":
            messagebox.showinfo(APP_NAME, "Use a custom profile name.")
            return
        cfg = load_json(MAP_PATH, {})
        self.save_settings()
        data = {"map": cfg, "settings": self.settings}
        path = os.path.join(PROFILES_DIR, name + ".json")
        save_json(path, data)
        self.refresh_profiles()
        self.write("Profile saved: %s" % name)

    def load_profile(self):
        name = self.profile_var.get().strip() or "default"
        if name == "default":
            return
        path = os.path.join(PROFILES_DIR, name + ".json")
        data = load_json(path, None)
        if not data:
            messagebox.showerror(APP_NAME, "Profile not found.")
            return
        try:
            save_json(MAP_PATH, data.get("map", {}))
            save_json(SETTINGS_PATH, data.get("settings", {}))
            self.settings = data.get("settings", self.settings)
            # reload UI vars
            self.dz_var.set(self.settings.get("deadzone", 0.08))
            self.dz_l_var.set(self.settings.get("deadzone_l", self.settings.get("deadzone", 0.08)))
            self.dz_r_var.set(self.settings.get("deadzone_r", self.settings.get("deadzone", 0.08)))
            self.curve_var.set(str(self.settings.get("curve", 1.0)))
            self.anti_var.set(self.settings.get("anti_deadzone", 0.0))
            self.trigthr_var.set(self.settings.get("trigger_threshold", 0))
            self.output_var.set(self.settings.get("output_mode", "xbox360"))
            self.hide_var.set(self.settings.get("hide_original", False))
            self.remap_var.set(json.dumps(self.settings.get("button_remap", {})))
            self.write("Profile loaded: %s" % name)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Load failed: %s" % exc)

    def pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.write(msg[1])
                elif kind == "status":
                    self.status.config(text=msg[1], fg=self.COLORS.get(msg[2], "#000000"))
                elif kind == "pad":
                    self.pad_lbl.config(text=("Pad: " + msg[1]) if msg[1] else "")
                elif kind == "raw":
                    self.raw_lbl.config(text=msg[1])
                elif kind == "hide_status":
                    self.hide_status.config(text=msg[1])
                elif kind == "prompt":
                    self.prompt.config(text=msg[1])
                elif kind == "progress":
                    self.progress.config(value=100.0 * msg[1] / max(1, msg[2]))
                elif kind == "mode":
                    self.set_mode(msg[1])
                elif kind == "calibrated":
                    pass
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def set_mode(self, mode):
        idle = mode == "idle"
        self.btn_start.config(state="normal" if idle else "disabled")
        self.btn_cal.config(state="normal" if idle else "disabled")
        self.btn_raw.config(state="normal" if idle else "disabled")
        self.btn_stop.config(state="disabled" if idle else "normal")
        self.btn_skip.config(state="normal" if mode == "calib" else "disabled")
        if idle:
            self.progress.config(value=0)

    # ---- actions -----------------------------------------------------------
    def name_filter(self):
        return self.settings.get("name") or None

    def start_calibration(self):
        self.engine.start("calib", self.engine.calibrate, self.name_filter())

    def start_raw(self):
        if self.engine.busy():
            return
        self.engine.start("raw", self.engine.raw_view, self.name_filter())

    def start_bridge(self):
        cfg = load_json(MAP_PATH, None)
        if not cfg:
            messagebox.showinfo(APP_NAME, "Please run Calibrate first.")
            return
        self.save_settings()
        self.settings["deadzone"] = round(self.dz_var.get(), 3)
        self.settings.setdefault("deadzone_l", self.settings["deadzone"])
        self.settings.setdefault("deadzone_r", self.settings["deadzone"])
        self.settings.setdefault("curve", 1.0)
        self.settings.setdefault("anti_deadzone", 0.0)
        self.settings.setdefault("trigger_threshold", 0)
        self.settings.setdefault("output_mode", "xbox360")
        save_json(SETTINGS_PATH, self.settings)
        self.status.config(text="Starting...", fg=self.COLORS["orange"])
        self.engine.start("bridge", self.engine.bridge, cfg,
                          self.settings, self.name_filter())

    def stop_all(self):
        self.engine.request_stop()

    def on_close(self):
        self.save_settings()
        if getattr(self, "tray_icon", None) is not None:
            self.root.withdraw()
            return
        self.engine.request_stop()
        if self.engine.thread is not None:
            self.engine.thread.join(timeout=2.0)
        self.root.destroy()

    def exit_app(self):
        self.save_settings()
        self.engine.request_stop()
        if self.engine.thread is not None:
            self.engine.thread.join(timeout=2.0)
        if getattr(self, "tray_icon", None) is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()


def main():
    autostart = "--autostart" in sys.argv
    root = tk.Tk()
    app = App(root, autostart)
    app.tray_icon = None
    if TRAY_AVAILABLE:
        try:
            def make_icon():
                img = Image.new("RGB", (64, 64), "#1a9a3a")
                d = ImageDraw.Draw(img)
                d.ellipse((8, 8, 56, 56), outline="white", width=4)
                d.text((26, 22), "P", fill="white")
                return img

            def show_window(icon, item):
                root.after(0, root.deiconify)

            def start_bridge(icon, item):
                root.after(0, app.start_bridge)

            def stop_all(icon, item):
                root.after(0, app.stop_all)

            def calibrate(icon, item):
                root.after(0, app.start_calibration)

            def quit_app(icon, item):
                root.after(0, app.exit_app)

            menu = pystray.Menu(
                pystray.MenuItem("Show", show_window),
                pystray.MenuItem("Start bridge", start_bridge),
                pystray.MenuItem("Stop", stop_all),
                pystray.MenuItem("Calibrate", calibrate),
                pystray.MenuItem("Quit", quit_app),
            )
            icon = pystray.Icon("Pad2Xbox", make_icon(), "Pad2Xbox", menu)
            app.tray_icon = icon
            threading.Thread(target=icon.run, daemon=True).start()
        except Exception as exc:
            app.write("Tray icon unavailable: %s" % exc)
    root.mainloop()


if __name__ == "__main__":
    main()
