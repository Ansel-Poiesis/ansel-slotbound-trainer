"""Atomic local save editing; uses the owner's game LuaJIT only when requested."""
from contextlib import nullcontext
import ctypes
import json
import os
from pathlib import Path
import shutil
import tempfile

from game_paths import lua_library

SAVE_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "slotbound_full"
SAVE = str(SAVE_DIR / "save_fallback.dat")
SAVE_BAK = SAVE + ".sbmod_bak"


class LuaJIT:
    def __init__(self):
        self._l = ctypes.CDLL(str(lua_library()))
        self._l.luaL_newstate.restype = ctypes.c_void_p
        self._l.luaL_openlibs.argtypes = [ctypes.c_void_p]
        self._l.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._l.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._l.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        self._l.lua_tolstring.restype = ctypes.c_char_p
        self._l.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._l.lua_close.argtypes = [ctypes.c_void_p]
        self.L = self._l.luaL_newstate()
        if not self.L:
            raise RuntimeError("无法创建 LuaJIT 存档处理状态")
        self._l.luaL_openlibs(self.L)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.L:
            self._l.lua_close(self.L)
            self.L = None

    def _laststr(self):
        value = self._l.lua_tolstring(self.L, -1, ctypes.byref(ctypes.c_size_t()))
        self._l.lua_settop(self.L, 0)
        return (value or b"Lua error").decode("utf-8", "replace")

    def dostring(self, code):
        if self._l.luaL_loadstring(self.L, code.encode("utf-8")):
            raise RuntimeError("lua load: " + self._laststr())
        if self._l.lua_pcall(self.L, 0, 1, 0):
            raise RuntimeError("lua run: " + self._laststr())
        result = self._l.lua_tolstring(self.L, -1, ctypes.byref(ctypes.c_size_t()))
        self._l.lua_settop(self.L, 0)
        return result.decode("utf-8", "replace") if result else ""


SAVE_SER = r'''
local function ser(v)
  local t = type(v)
  if t == "number" then
    if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end
    return string.format("%.17g", v)
  elseif t == "string" then return string.format("%q", v)
  elseif t == "boolean" then return tostring(v)
  elseif t == "table" then
    local parts, seen = {}, {}
    for i = 1, #v do parts[#parts+1] = ser(v[i]); seen[i] = true end
    local keys = {}
    for k in pairs(v) do
      if not (type(k) == "number" and seen[k]) then keys[#keys+1] = k end
    end
    table.sort(keys, function(a,b) return tostring(a) < tostring(b) end)
    for _, k in ipairs(keys) do parts[#parts+1] = "[" .. ser(k) .. "]=" .. ser(v[k]) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  error("cannot serialize " .. t)
end
'''


def game_running():
    from runtime_patch import GameMemory
    try:
        GameMemory._find_pid()
    except RuntimeError:
        return False
    return True


def edit_save(ops, lua_state=None):
    path = Path(SAVE)
    original = path.read_bytes()
    with (LuaJIT() if lua_state is None else nullcontext(lua_state)) as lua:
        source = json.dumps(original.decode("utf-8-sig"), ensure_ascii=False)
        script = SAVE_SER + (
            "local src = %s local f, err = loadstring(src, 'save') if not f then error(err) end "
            "local s = f() %s local out = 'return ' .. ser(s) "
            "assert(type(assert(loadstring(out))()) == 'table') return out" % (source, "\n".join(ops)))
        result = lua.dostring(script).encode("utf-8")
    if not Path(SAVE_BAK).exists():
        shutil.copyfile(SAVE, SAVE_BAK)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sbmod-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            raise RuntimeError("存档在编辑期间发生变化，请重新读取后重试")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return "ok"


def read_save_field(expr):
    source = json.dumps(Path(SAVE).read_text(encoding="utf-8-sig"), ensure_ascii=False)
    with LuaJIT() as lua:
        return lua.dostring("local s = assert(loadstring(%s))() return tostring(%s)" % (source, expr))
