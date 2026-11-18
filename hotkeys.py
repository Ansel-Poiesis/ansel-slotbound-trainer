"""Foreground-scoped Windows hotkeys for the trainer and the target game."""

import ctypes
from ctypes import wintypes
import os
import queue
import threading


SHORTCUTS = {0: ("Num 0", "quick_supply"), 1: ("Num 1", "draw"), 2: ("Num 2", "once_win"),
             3: ("Num 3", "add_spins"), 4: ("Num 4", "add_coins"),
             5: ("Num 5", "base_hp"), 6: ("Num 6", "paid_free"),
             7: ("Num 7", "grade"), 8: ("Num 8", "reward"),
             9: ("Num 9", "battle_speed")}


class Hotkeys(threading.Thread):
    def __init__(self):
        super().__init__(name="slotbound-hotkeys", daemon=True)
        self.events = queue.Queue()
        self.stop_requested = threading.Event()
        self.game_pid = None
        self.foreground_pid = None
        self.paused = False
        self.registered = set()
        self.failed = set()

    def run(self):
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        message = wintypes.MSG()
        previous = None
        try:
            while not self.stop_requested.wait(.05):
                foreground_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(foreground_pid))
                self.foreground_pid = foreground_pid.value
                active = foreground_pid.value in {os.getpid(), self.game_pid}
                wanted = ({100} | (set(SHORTCUTS) if not self.paused else set())) if active else set()
                for identifier in self.registered - wanted:
                    user32.UnregisterHotKey(None, identifier)
                self.registered &= wanted
                for identifier in wanted - self.registered:
                    modifiers = 0x4000 | (6 if identifier == 100 else 0)
                    key = 0x24 if identifier == 100 else 0x60 + identifier
                    if user32.RegisterHotKey(None, identifier, modifiers, key):
                        self.registered.add(identifier)
                        self.failed.discard(identifier)
                    else:
                        self.failed.add(identifier)
                state = (active, self.paused, tuple(sorted(self.failed & wanted)))
                if state != previous:
                    self.events.put(("status", state))
                    previous = state
                while user32.PeekMessageW(ctypes.byref(message), None, 0x312, 0x312, 1):
                    identifier = int(message.wParam)
                    if identifier == 100:
                        self.events.put(("action", "pause"))
                    elif identifier in SHORTCUTS and not self.paused:
                        self.events.put(("action", SHORTCUTS[identifier][1]))
        finally:
            for identifier in self.registered:
                user32.UnregisterHotKey(None, identifier)
