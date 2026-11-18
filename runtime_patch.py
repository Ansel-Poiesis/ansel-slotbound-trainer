# -*- coding: utf-8 -*-
"""Read and update Slotbound's running LuaJIT GC64 tables.

GCstr data starts at +24. GCtab array, node, asize and hmask are located
at +16, +40, +48 and +52. Hash nodes occupy 24 bytes.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import subprocess
import math
import re

k32 = ctypes.windll.kernel32
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
STR_TAG = 0x1FFFB << 47
TAB_TAG = 0x1FFF4 << 47
PTR_MASK = (1 << 47) - 1


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
                ("AllocationProtect", wt.DWORD), ("_a", wt.DWORD),
                ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_b", wt.DWORD)]


class GameMemory:
    def __init__(self, pid=None):
        if pid is None:
            pid = self._find_pid()
        self.pid = pid
        self.h = k32.OpenProcess(0x0438, False, pid)  # READ|WRITE|OP|QUERY
        if not self.h:
            raise RuntimeError("无法打开 Slotbound 进程(需游戏正在运行)")
        self._scan_cache = None
        self._chances_cache = {}
        self._node_cache = {}
        self._roots = {}
        self._patch_addrs = []

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None
        self._scan_cache = None
        self._node_cache.clear()
        self._roots.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _find_pid():
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq Slotbound.exe"],
                                      text=True, errors="replace", creationflags=0x08000000)
        for ln in out.splitlines():
            if ln.lower().startswith("slotbound.exe"):
                return int(ln.split()[1])
        raise RuntimeError("Slotbound 未运行")

    # ---------- 基础 ----------
    def read(self, addr, size):
        b = (ctypes.c_ubyte * size)()
        n = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), b, size,
                                     ctypes.byref(n)):
            return None
        return bytes(b) if n.value == size else None

    def read_q(self, addr):
        d = self.read(addr, 8)
        return struct.unpack("<Q", d)[0] if d else None

    def read_d(self, addr):
        d = self.read(addr, 8)
        if d is None:
            return None
        value = struct.unpack("<d", d)[0]
        return value if math.isfinite(value) else None

    def write(self, addr, data):
        n = ctypes.c_size_t()
        ok = k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data,
                                    len(data), ctypes.byref(n))
        return ok and n.value == len(data)

    def scan(self, needle, maxhits=40, step=1):
        """Read private memory in bounded chunks, retaining boundary matches."""
        if not needle or maxhits <= 0:
            return []
        hits = []
        tail, end = b"", None
        next_allowed = 0
        for base, data in self.private_chunks():
            if base != end:
                tail = b""
            block = tail + data
            origin = base - len(tail)
            pos = block.find(needle, max(0, next_allowed - origin))
            while pos >= 0:
                hits.append(origin + pos)
                if len(hits) >= maxhits:
                    return hits
                next_allowed = origin + pos + max(1, step)
                pos = block.find(needle, pos + max(1, step))
            end = base + len(data)
            tail = block[-(len(needle) - 1):] if len(needle) > 1 else b""
        return hits

    def private_chunks(self, chunk_size=4 * 1024 * 1024):
        """Yield live readable heap chunks without retaining a process snapshot."""
        address = 0
        info = MBI()
        while address < 0x7FFFFFFEFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)):
                break
            if info.State == 0x1000 and info.Type == 0x20000 and info.Protect & 0xff in (2, 4, 8, 32, 64, 128) and not (info.Protect & 0x100):
                offset = 0
                while offset < info.RegionSize:
                    size = min(chunk_size, info.RegionSize - offset)
                    data = self.read(info.BaseAddress + offset, size)
                    if data:
                        yield info.BaseAddress + offset, data
                    offset += size
            address = info.BaseAddress + info.RegionSize

    def _snapshot(self):
        """把可读内存区快照成单一大 blob(含地址映射),加速多次搜索"""
        parts = []
        bases = []
        addr = 0
        mbi = MBI()
        buf = ctypes.create_string_buffer(4 * 1024 * 1024)
        while addr < 0x7FFFFFFEFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                                      ctypes.sizeof(mbi)):
                break
            if mbi.State == 0x1000 and mbi.Type == 0x20000 and mbi.Protect & 0xff in (2, 4, 8, 32, 64, 128) and not (mbi.Protect & 0x100):
                off = 0
                while off < mbi.RegionSize:
                    chunk = min(mbi.RegionSize - off, len(buf))
                    n = ctypes.c_size_t()
                    if k32.ReadProcessMemory(self.h, ctypes.c_void_p(mbi.BaseAddress + off),
                                             buf, chunk, ctypes.byref(n)) and n.value:
                        parts.append(buf.raw[:n.value])
                        bases.append(mbi.BaseAddress + off)
                    off += chunk
            addr = mbi.BaseAddress + mbi.RegionSize
        SEP = b"\x00" * 64
        blob = b"".join(p + SEP for p in parts)
        starts = []
        pos = 0
        for p, b in zip(parts, bases):
            starts.append((pos, pos + len(p), b))
            pos += len(p) + len(SEP)
        return blob, starts

    def invalidate(self):
        self._scan_cache = None
        self._node_cache.clear()

    # ---------------- watchdog 轻量重注入 ----------------
    _patch_addrs = None   # [(addr, double_value)]
    _last_unique_addr = None
    _last_table_addr = None
    _last_array_addr = None

    def rescan_near(self, needle, center, radius=48 * 1024 * 1024, maxhits=8):
        """在 center±radius 的私有可读内存实时搜 needle(用于重定位被替换的表)"""
        import ctypes as _ct
        lo, hi = max(0, center - radius), center + radius
        addr = lo
        mbi = MBI()
        buf = _ct.create_string_buffer(4 * 1024 * 1024)
        hits = []
        while addr < hi and len(hits) < maxhits:
            if not k32.VirtualQueryEx(self.h, _ct.c_void_p(addr), _ct.byref(mbi), _ct.sizeof(mbi)):
                break
            rlo, rhi = max(mbi.BaseAddress, lo), min(mbi.BaseAddress + mbi.RegionSize, hi)
            if mbi.State == 0x1000 and mbi.Type == 0x20000 and mbi.Protect not in (0x01, 0x100) \
                    and not (mbi.Protect & 0x100) and rhi > rlo:
                off = rlo - mbi.BaseAddress
                while off < rlo - mbi.BaseAddress + (rhi - rlo):
                    chunk = min(rhi - rlo - (off - (rlo - mbi.BaseAddress)), len(buf))
                    n = _ct.c_size_t()
                    if k32.ReadProcessMemory(self.h, _ct.c_void_p(mbi.BaseAddress + off),
                                             buf, chunk, _ct.byref(n)) and n.value:
                        data = buf.raw[:n.value]
                        p = data.find(needle)
                        while p >= 0 and len(hits) < maxhits:
                            hits.append(mbi.BaseAddress + off + p)
                            p = data.find(needle, p + 1)
                    off += chunk
            addr = mbi.BaseAddress + mbi.RegionSize
        return hits

    _sw_gcstr = None
    _sw_tab = None

    def refresh_symbol_weights(self, weights, allow_rescan=True):
        """确保当前生效的 symbolWeights 表 = weights。
        原理:symbolWeights 的键节点位于 slot 表内,地址终身稳定;
        其 val 即"当前生效表"指针(游戏整体替换表时只改这个指针)。
        → 直接读键节点取当前表,零搜索、永不丢。"""
        if getattr(self, "_sw_keynode", None) is None:
            gs = self.find_gcstr("symbolWeights")
            if not gs:
                return False
            for kf, va, val_q in self.find_key_nodes(gs[0]):
                vq = self.read_q(va)
                if vq and (vq >> 47) == (TAB_TAG >> 47):
                    self._sw_keynode = kf
                    break
            if getattr(self, "_sw_keynode", None) is None:
                return False
        kf = self._sw_keynode
        vq = self.read_q(kf - 8)
        if not vq or (vq >> 47) != (TAB_TAG >> 47):
            # 进程重启或异常:重置锚点
            self._sw_keynode = None
            return False
        tab = vq & PTR_MASK
        if not self.read_q(tab + 0x28):
            self._sw_keynode = None
            return False
        self._sw_tab = tab
        nodes = self.table_nodes(tab)
        if not any(k in nodes for k in weights):
            self._sw_keynode = None
            return False
        changed = 0
        for k, v in weights.items():
            a = nodes.get(k)
            if not a:
                continue
            cur = self.read_d(a)
            if cur is None or abs(cur - v) > 1e-9:
                if self.write(a, struct.pack("<d", float(v))):
                    changed += 1
        self._sw_changed = changed
        return True

    def record_patch(self, addr, value):
        self._patch_addrs = [(a, v) for a, v in self._patch_addrs if a != addr]
        self._patch_addrs.append((addr, float(value)))

    def reapply_cached(self):
        """按已记录地址重写(同进程);地址失效返回 False(调用方应重建快照)"""
        if not self._patch_addrs:
            return False
        import struct as _s
        bad = 0
        for addr, val in self._patch_addrs:
            if not self.write(addr, _s.pack('<d', val)):
                bad += 1
        return bad == 0

    def alive(self):
        code = wt.DWORD()
        return bool(self.h and k32.GetExitCodeProcess(self.h, ctypes.byref(code))
                    and code.value == 259)

    # ---------- LuaJIT 结构 ----------
    def find_gcstr(self, s):
        """返回 GCstr 对象基址列表(数据区-24)"""
        data = s.encode() if isinstance(s, str) else s
        hits = self.scan(data + b"\x00", maxhits=4096)
        out = []
        for hpos in hits:
            base = hpos - 24
            if (base % 8 == 0 and self.read(base + 9, 1) == b"\x04"
                    and self.read(base + 20, 4) == struct.pack("<I", len(data))):
                out.append(base)
        return out

    def find_key_nodes(self, gcstr_base):
        """所有 key==gcstr 的 Node,返回 [(key_field_addr, val_addr, val_q)]"""
        tagged = STR_TAG | gcstr_base
        hits = self.scan(struct.pack("<Q", tagged), maxhits=256)
        out = []
        for kf in hits:  # kf = key 字段地址;val 在 kf-8
            val_q = self.read_q(kf - 8)
            if val_q is not None:
                out.append((kf, kf - 8, val_q))
        return out

    def table_nodes(self, tab_addr, max_slots=256):
        """Read the GC64 table's actual hash allocation, including large globals."""
        header = self.read(tab_addr, 56)
        if not header or header[9] != 11:
            return {}
        node_ptr = struct.unpack_from("<Q", header, 40)[0]
        hmask = struct.unpack_from("<I", header, 52)[0]
        if not node_ptr or hmask > 1048575 or hmask & (hmask + 1):
            return {}
        identity = (node_ptr, hmask)
        cached = self._node_cache.get(tab_addr)
        if cached and cached[0] == identity:
            return cached[1]
        block = self.read(node_ptr, (hmask + 1) * 24)
        if block is None:
            return {}
        out = {}
        for i in range(hmask + 1):
            base = node_ptr + i * 24
            val, key = struct.unpack_from("<QQ", block, i * 24)
            if (key >> 47) == (STR_TAG >> 47):
                saddr = key & PTR_MASK
                length_data = self.read(saddr + 20, 4)
                length = struct.unpack("<I", length_data)[0] if length_data else 0
                if 0 < length <= 512:
                    sdata = self.read(saddr + 24, length)
                    if sdata:
                        out[sdata.decode("utf-8", "replace")] = base
        self._node_cache[tab_addr] = (identity, out)
        return out

    def table_field(self, table, key):
        if isinstance(key, str):
            address = self.table_nodes(table).get(key)
            if address is not None:
                tagged = self.read_q(address + 8)
                if tagged is not None and tagged >> 47 == STR_TAG >> 47:
                    pointer = tagged & PTR_MASK
                    name = key.encode("utf-8")
                    if (self.read(pointer + 20, 4) == struct.pack("<I", len(name))
                            and self.read(pointer + 24, len(name)) == name):
                        return address
            # Keys can move within an unchanged allocation, and freed tables
            # can be reused at the same address. Validate before trusting a hit.
            self._node_cache.pop(table, None)
            return self.table_nodes(table).get(key)
        header = self.read(table, 56)
        if not header:
            return None
        array = struct.unpack_from("<Q", header, 16)[0]
        asize = struct.unpack_from("<I", header, 48)[0]
        if array and 0 <= key < asize:
            return array + key * 8
        node = struct.unpack_from("<Q", header, 40)[0]
        hmask = struct.unpack_from("<I", header, 52)[0]
        if node and hmask <= 1048575:
            block = self.read(node, (hmask + 1) * 24)
            if block:
                for index in range(hmask + 1):
                    candidate = struct.unpack_from("<d", block, index * 24 + 8)[0]
                    if math.isfinite(candidate) and candidate == key:
                        return node + index * 24
        return None

    def root_table(self, root="constants"):
        cached = self._roots.get(root)
        required = {"constants": ("slot", "economy", "hiddenLuck"),
                    "globals": ("math", "love", "package")}[root]
        if cached and all(key in self.table_nodes(cached) for key in required):
            return cached
        key = "src.data.constants" if root == "constants" else "_G"
        for gcstr in self.find_gcstr(key):
            for _, _, value in self.find_key_nodes(gcstr):
                if value >> 47 != TAB_TAG >> 47:
                    continue
                table = value & PTR_MASK
                if all(name in self.table_nodes(table) for name in required):
                    self._roots[root] = table
                    return table
        raise RuntimeError("等待游戏载入" if root == "constants" else "当前游戏状态尚未就绪")

    def resolve_path(self, path, root="constants"):
        parts = re.findall(r"([^\.\[\]]+)|\[(\d+)\]", path)
        if not parts:
            raise ValueError("空参数路径")
        table = self.root_table(root)
        for index, (name, number) in enumerate(parts):
            key = int(number) if number else name
            address = self.table_field(table, key)
            if address is None:
                raise RuntimeError("当前场景缺少 " + path)
            if index == len(parts) - 1:
                return address
            tagged = self.read_q(address)
            if tagged is None or tagged >> 47 != TAB_TAG >> 47:
                raise RuntimeError("当前场景缺少 " + path)
            table = tagged & PTR_MASK

    def read_path(self, path, root="constants"):
        value = self.read_d(self.resolve_path(path, root))
        if value is None:
            raise RuntimeError("参数当前不是有效数值 " + path)
        return value

    def table_array_indices(self, path, root="globals"):
        """Indices of dense Lua array entries containing tables, used by rosters."""
        try:
            raw = self.read_q(self.resolve_path(path, root))
        except RuntimeError:
            return []
        if raw is None or raw >> 47 != TAB_TAG >> 47:
            return []
        table = raw & PTR_MASK
        header = self.read(table, 56)
        if not header:
            return []
        array = struct.unpack_from("<Q", header, 16)[0]
        size = struct.unpack_from("<I", header, 48)[0]
        if not array or size > 65536:
            return []
        block = self.read(array, size * 8)
        if not block:
            return []
        return [index for index in range(1, size)
                if struct.unpack_from("<Q", block, index * 8)[0] >> 47 == TAB_TAG >> 47]

    def write_number(self, address, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("数值必须有限")
        if not self.write(address, struct.pack("<d", value)):
            raise RuntimeError("写入失败，目标对象可能已变化")
        actual = self.read_d(address)
        if actual is None or not math.isclose(actual, value, rel_tol=1e-10, abs_tol=1e-10):
            raise RuntimeError("写入回读不一致")

    def read_string_path(self, path, root="globals"):
        tagged = self.read_q(self.resolve_path(path, root))
        if tagged is None or tagged >> 47 != STR_TAG >> 47:
            return ""
        pointer = tagged & PTR_MASK
        length = self.read(pointer + 20, 4)
        if not length:
            return ""
        data = self.read(pointer + 24, min(struct.unpack("<I", length)[0], 256))
        return data.decode("utf-8", "replace") if data else ""

    def read_boolean(self, address):
        value = self.read_q(address)
        if value == 0xffff7fffffffffff:
            return False
        if value == 0xfffeffffffffffff:
            return True
        return None

    def write_boolean(self, address, value):
        tagged = (~((2 if value else 1) << 47)) & 0xffffffffffffffff
        if not self.write(address, struct.pack("<Q", tagged)):
            raise RuntimeError("布尔参数写入失败")
        actual = self.read_q(address)
        if actual != tagged:
            raise RuntimeError("布尔参数回读失败")

    def read_scalar(self, address):
        """Read a Lua number, boolean, or nil without treating tags as numbers."""
        raw = self.read_q(address)
        if raw == 0xffffffffffffffff:
            return None
        boolean = self.read_boolean(address)
        if boolean is not None:
            return boolean
        value = self.read_d(address)
        if value is None:
            raise RuntimeError("参数不是数值、布尔或空值")
        return value

    def write_scalar(self, address, value):
        if isinstance(value, bool):
            self.write_boolean(address, value)
        elif value is None:
            raw = struct.pack("<Q", 0xffffffffffffffff)
            if not self.write(address, raw) or self.read_q(address) != 0xffffffffffffffff:
                raise RuntimeError("空值恢复回读失败")
        else:
            self.write_number(address, value)

    def get_table(self, key_name, occurrence=0):
        """通过唯一字符串键定位其 value 为 table 的 GCtab 地址"""
        strs = self.find_gcstr(key_name)
        for g in strs:
            for kf, va, val_q in self.find_key_nodes(g):
                if (val_q >> 47) == (TAB_TAG >> 47):
                    tab = val_q & PTR_MASK
                    # 校验像 GCtab:node 指针可读
                    np = self.read_q(tab + 0x28)
                    if np and self.read(np, 8):
                        return tab
        return None

    # ---------- 修改 API ----------
    def set_table_value(self, table_addr, key_name, new_value, expect_old=None):
        """在指定表 hash part 里改 key 的值(数字)。返回 (ok, old)"""
        nodes = self.table_nodes(table_addr)
        if key_name not in nodes:
            return False, None
        addr = nodes[key_name]
        old = self.read_d(addr)
        if expect_old is not None and abs((old or 0) - expect_old) > 1e-9:
            return False, old
        if not self.write(addr, struct.pack("<d", float(new_value))):
            return False, old
        self._last_table_addr = addr
        return True, old

    def set_global_unique(self, key_name, new_value, expect_old=None, choose=None):
        """对键名唯一的参数:改全部引用点中值匹配的那个"""
        strs = self.find_gcstr(key_name)
        for g in strs:
            for kf, va, val_q in self.find_key_nodes(g):
                old = self.read_d(va)
                if old is None:
                    continue
                if expect_old is not None and abs(old - expect_old) > 1e-9:
                    continue
                if self.write(va, struct.pack("<d", float(new_value))):
                    self._last_unique_addr = va
                    return True, old
        return False, None

    def set_array_value(self, table_addr, index_1based, new_value, expect_old=None,
                        tsize=8):
        """改 GCtab array part 第 n 个元素(8B/元素,数字=double,nil=0xFF 填充)"""
        arr = self.read_q(table_addr + 0x10)
        if not arr:
            return False, None
        addr = arr + index_1based * tsize
        old = self.read_d(addr)
        if expect_old is not None and (old is None or abs(old - expect_old) > 1e-9):
            return False, old
        if not self.write(addr, struct.pack("<d", float(new_value))):
            return False, old
        self._last_array_addr = addr
        return True, old

    def read_array(self, table_addr, n=10, tsize=8):
        arr = self.read_q(table_addr + 0x10)
        if not arr:
            return []
        blk = self.read(arr, n * tsize)
        if not blk:
            return []
        out = []
        for i in range(n):
            q = struct.unpack_from("<Q", blk, i * tsize)[0]
            if q == 0xFFFFFFFFFFFFFFFF:
                out.append(None)
            else:
                out.append(struct.unpack_from("<d", blk, i * tsize)[0])
        return out

    # chances 数组特征(用于区分 4 个同名 chances 表)
    CHANCES_SIGS = {
        "round":  [None, 0.7, 0.7, 0.7, 0.7, 0.45, 0.2],
        "streak": [None, None, None, 0.15, 0.3, 0.5, 0.75],
        "early2": [None, None, 0.25, 0.6, 1],
        "early3": [None, None, None, 0.2, 0.55, 1],
    }

    _chances_cache = {}

    # 值无关的结构特征: (nil前缀槽数, 值元素个数) — 与具体数值无关,改值后仍可识别
    CHANCES_SHAPE = {"round": (2, 6), "streak": (4, 4),
                     "early2": (3, 3), "early3": (4, 3)}

    def _shape_of(self, tab, max_slots=12):
        """返回 (nil前缀数, 连续值数);值判定: >1e-6 的有限数"""
        arr = self.read_q(tab + 0x10)
        if not arr:
            return None
        blk = self.read(arr, max_slots * 8)
        if not blk:
            return None
        i = 0
        while i < max_slots:
            q = struct.unpack_from("<Q", blk, i * 8)[0]
            if q == 0xFFFFFFFFFFFFFFFF:
                i += 1
            else:
                break
        prefix = i
        vals = 0
        while i < max_slots:
            q = struct.unpack_from("<Q", blk, i * 8)[0]
            if q == 0xFFFFFFFFFFFFFFFF:
                break
            v = struct.unpack_from("<d", blk, i * 8)[0]
            if v == v and abs(v) > 1e-6 and abs(v) < 1e6:
                vals += 1
                i += 1
            else:
                break
        return (prefix, vals)

    def find_chances_table(self, which):
        """定位 chances 子表:缓存 → 值序列精确匹配 → 结构特征匹配"""
        if which in self._chances_cache:
            tab = self._chances_cache[which]
            if self.read_q(tab + 0x10):
                return tab
        sig = [v for v in self.CHANCES_SIGS[which] if v is not None]
        shape = self.CHANCES_SHAPE[which]
        exact = None
        by_shape = None
        gs = self.find_gcstr("chances")
        for g in gs:
            for kf, va, val_q in self.find_key_nodes(g):
                vq = self.read_q(va)
                if vq and (vq >> 47) == (TAB_TAG >> 47):
                    tab = vq & PTR_MASK
                    cur = [v for v in self.read_array(tab, shape[0] + shape[1] + 4)
                           if v is not None]
                    if len(cur) >= len(sig) and                        all(abs(a - b) < 1e-9 for a, b in zip(cur, sig)):
                        exact = exact or tab
                    sh = self._shape_of(tab)
                    if sh == shape:
                        by_shape = by_shape or tab
        result = exact or by_shape
        if result:
            self._chances_cache[which] = result
        return result

    def set_chances_idx(self, which, lua_idx, new_value):
        """改 chances[Lua 索引]。实测槽位 = Lua 索引(0-based 槽 i ↔ Lua[i+1],
        nil 头占槽)。校验旧值;若已被本工具改过(expect 不符但为有限数值)也接受。"""
        sig = self.CHANCES_SIGS[which]
        if lua_idx - 1 >= len(sig):
            return False, None
        expect = sig[lua_idx - 1]
        tab = self.find_chances_table(which)
        if not tab:
            return False, None
        if expect is None:
            return False, "nil 位置不可改"
        arr = self.read_q(tab + 0x10)
        addr = arr + lua_idx * 8
        old = self.read_d(addr)
        if old is None:
            return False, None
        if abs(old - expect) > 1e-9:
            # 已被修改过:确认是有限数值(且在 0~1 合理区间)再覆盖
            if not (old == old and -1e6 < abs(old) <= 1e6):
                return False, old
        if self.write(addr, struct.pack("<d", float(new_value))):
            self._last_array_addr = addr
            return True, old
        return False, old


# ---------------------------------------------------------------- 参数应用
def build_patch_ops(consts_edits, expect=True):
    from sbmod_params import CONSTANTS_EDITS
    definitions = CONSTANTS_EDITS["src/data/constants.lua"]
    operations = []
    for path, value in consts_edits.items():
        if path not in definitions:
            raise ValueError("运行时模式暂不支持 " + path)
        original = definitions[path][0] if expect else None
        operations.append(("path", path, value, original))
    return operations


def apply_runtime(gm, ops, log=print):
    results = []
    for operation in ops:
        _, path, value, expected = operation
        try:
            address = gm.resolve_path(path)
            previous = gm.read_d(address)
            if previous is None:
                raise RuntimeError("参数为非数值")
            if expected is not None and not math.isclose(previous, expected, rel_tol=1e-9, abs_tol=1e-9):
                raise RuntimeError("当前值与预期原值不符")
            gm.write_number(address, value)
            gm.record_patch(address, value)
            log("  %s  %g → %g，回读一致" % (path, previous, value))
            results.append(True)
        except (RuntimeError, ValueError) as error:
            log("  %s  失败，%s" % (path, error))
            results.append(False)
    return all(results)


if __name__ == "__main__":
    import sys, json, time
    # 等游戏启动稳定
    gm = GameMemory()
    print("已连接 Slotbound pid=%d,构建内存快照..." % gm.pid)
    t0 = time.time()
    # 自检:定位 symbolWeights 并读值
    tab = gm.get_table("symbolWeights")
    print("symbolWeights 表 @ %s (%.1fs)" % (hex(tab) if tab else None, time.time() - t0))
    if tab:
        nodes = gm.table_nodes(tab)
        for k, addr in nodes.items():
            if not k.startswith("__"):
                print("  %s = %g" % (k, gm.read_d(addr)))
