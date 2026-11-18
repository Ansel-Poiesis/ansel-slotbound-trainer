# -*- coding: utf-8 -*-
"""CE 式数值扫描器:任意 double 值的 首扫→再扫收敛→改值→锁定。
基于 LuaJIT 所有数字都是 double 的事实,可定位游戏内任意数值
(金币/血量/伤害/道具数...),突破 constants 静态参数的边界。"""
import ctypes
import struct

from runtime_patch import GameMemory, k32, MBI


class ValueScanner:
    """扫描-收敛-锁定流程(单实例绑定一个 GameMemory)"""

    def __init__(self, gm: GameMemory = None):
        self.gm = gm
        self.candidates = []      # [addr]
        self.round = 0            # 扫描轮次

    def bind(self, gm):
        self.gm = gm
        self.reset()

    def reset(self):
        self.candidates = []
        self.round = 0

    # ---------------------------------------------------------------- 扫描
    def first_scan(self, value, max_hits=200000):
        """全堆搜 double 值。返回命中数。"""
        if self.gm is None:
            raise RuntimeError("未连接游戏")
        needle = struct.pack("<d", float(value))
        self.gm.invalidate()
        self.candidates = self.gm.scan(needle, maxhits=max_hits, step=len(needle))
        self.round = 1
        return len(self.candidates)

    def next_scan(self, value, tol=1e-9):
        """在候选中过滤出当前值==value 的地址(实时读)。"""
        v = float(value)
        out = []
        for a in self.candidates:
            cur = self.gm.read_d(a)
            if cur is not None and abs(cur - v) <= tol:
                out.append(a)
        self.candidates = out
        self.round += 1
        return len(out)

    # ---------------------------------------------------------------- 操作
    def set_value(self, addr, value):
        self.gm.write_number(addr, float(value))
        return True

    def read(self, addr):
        return self.gm.read_d(addr)

    def lock_loop_tick(self, locks):
        """locks: [(addr, value)] 每周期写回"""
        for a, v in locks:
            self.gm.write_number(a, float(v))


if __name__ == "__main__":
    # 自测:对运行中的游戏扫一个已知值(naturalWinProbability=0.2274)
    import sys, time
    sys.path.insert(0, ".")
    gm = GameMemory()
    sc = ValueScanner(gm)
    t0 = time.time()
    n = sc.first_scan(0.2274)
    print("首扫 0.2274: %d 个地址 (%.1fs)" % (n, time.time() - t0))
    n2 = sc.next_scan(0.2274)
    print("再扫: %d 个" % n2)
    for a in sc.candidates[:6]:
        print("  %#x = %g" % (a, sc.read(a)))
