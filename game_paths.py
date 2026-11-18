"""Lazy, portable game discovery. Importing the trainer never loads game files."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

APP_ID = "4459590"


def config_file():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Ansel/SlotboundTrainer/settings.json"


def validate_directory(directory):
    path = Path(directory).expanduser().resolve()
    missing = [name for name in ("Slotbound.exe", "lua51.dll") if not (path / name).is_file()]
    if missing:
        raise ValueError("请选择含 Slotbound.exe 和 lua51.dll 的游戏目录；缺少：" + "、".join(missing))
    return path


def save_directory(directory):
    directory = validate_directory(directory)
    target = config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump({"game_directory": str(directory)}, stream, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return directory


def steam_roots():
    import winreg
    roots = set()
    for hive, key, field in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                roots.add(Path(winreg.QueryValueEx(handle, field)[0]))
        except FileNotFoundError:
            continue
    libraries = set(roots)
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            text = vdf.read_text(encoding="utf-8-sig")
            libraries.update(Path(value.replace("\\\\", "\\")) for value in
                             re.findall(r'"path"\s*"((?:\\.|[^"\\])*)"', text))
    return sorted(libraries)


def running_directory():
    output = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq Slotbound.exe", "/FO", "CSV", "/NH"],
                                     text=True, errors="replace", creationflags=0x08000000)
    import csv
    for row in csv.reader(output.splitlines()):
        if len(row) < 2 or row[0].lower() != "slotbound.exe":
            continue
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(row[1]))
        if not handle:
            continue
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).parent
        finally:
            kernel.CloseHandle(handle)
    return None


def find_game_directory():
    explicit = os.environ.get("SLOTBOUND_GAME_DIR")
    if explicit:
        return validate_directory(explicit)
    settings = config_file()
    if settings.is_file():
        try:
            saved = json.loads(settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("游戏目录配置无法读取，请重新选择游戏目录") from error
        if saved.get("game_directory"):
            candidate = Path(saved["game_directory"])
            if (candidate / "Slotbound.exe").is_file() and (candidate / "lua51.dll").is_file():
                return candidate.resolve()
    running = running_directory()
    if running and (running / "lua51.dll").is_file():
        return running.resolve()
    for library in steam_roots():
        manifest = library / ("steamapps/appmanifest_" + APP_ID + ".acf")
        if manifest.is_file():
            match = re.search(r'"installdir"\s*"([^"/\\]+)"', manifest.read_text(encoding="utf-8-sig"))
            if match:
                candidate = library / "steamapps/common" / match[1]
                if (candidate / "Slotbound.exe").is_file() and (candidate / "lua51.dll").is_file():
                    return candidate.resolve()
    return None


def lua_library():
    directory = find_game_directory()
    if directory is None:
        raise RuntimeError("未找到游戏安装目录，请点击顶部“游戏目录”选择；仅存档功能需要本机 lua51.dll")
    return directory / "lua51.dll"
