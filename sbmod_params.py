# -*- coding: utf-8 -*-
"""Slotbound 修改器 - 全部可调参数定义
结构:
  CONSTANTS_EDITS[文件][路径] = (原值, 说明, 分组)
  SAVE_PRESETS = [(名称, 说明, [lua 语句])]
供运行中的内存修改器和局外存档面板使用。
"""

CONSTANTS_EDITS = {
    "src/data/constants.lua": {
        # ---- 保底系统(核心) ----
        "hiddenLuck.streak.certainAt":    (8, "连败 N 次后 100% 必中(保底命中次数)", "保底系统"),
        "hiddenLuck.streak.minWave":      (4, "保底从第 N 波开始生效", "保底系统"),
        "hiddenLuck.streak.chances[4]":   (0.15, "连败4次强中概率", "保底系统"),
        "hiddenLuck.streak.chances[5]":   (0.3, "连败5次强中概率", "保底系统"),
        "hiddenLuck.streak.chances[6]":   (0.5, "连败6次强中概率", "保底系统"),
        "hiddenLuck.streak.chances[7]":   (0.75, "连败7次强中概率", "保底系统"),
        "hiddenLuck.round.fromSpin":      (3, "每波第 N 抽起开始有回合保底", "保底系统"),
        "hiddenLuck.round.chances[2]":    (0.7, "每波第2抽保底概率(未中过时)", "保底系统"),
        "hiddenLuck.round.chances[3]":    (0.7, "每波第3抽保底概率", "保底系统"),
        "hiddenLuck.round.chances[4]":    (0.7, "每波第4抽保底概率", "保底系统"),
        "hiddenLuck.round.chances[5]":    (0.7, "每波第5抽保底概率", "保底系统"),
        "hiddenLuck.round.chances[6]":    (0.45, "每波第6抽保底概率", "保底系统"),
        "hiddenLuck.round.chances[7]":    (0.2, "每波第7抽保底概率", "保底系统"),
        "hiddenLuck.early[2].chances[3]": (0.25, "第2波第3抽保底概率(前期保护)", "保底系统"),
        "hiddenLuck.early[2].chances[4]": (0.6, "第2波第4抽保底概率", "保底系统"),
        "hiddenLuck.early[2].chances[5]": (1, "第2波第5抽:必中", "保底系统"),
        "hiddenLuck.early[3].chances[4]": (0.2, "第3波第4抽保底概率", "保底系统"),
        "hiddenLuck.early[3].chances[5]": (0.55, "第3波第5抽保底概率", "保底系统"),
        "hiddenLuck.early[3].chances[6]": (1, "第3波第6抽:必中", "保底系统"),
        "hiddenLuck.naturalWinProbability": (0.2274, "自然中奖率基准(运气债累积速度)", "保底系统"),
        "hiddenLuck.debtMax":             (3, "运气债上限", "保底系统"),
        "hiddenLuck.debtMin":             (-3, "运气债下限", "保底系统"),
        # ---- 老虎机 ----
        "slot.forceWinProb":              (0, "强制中奖概率 0~1(0=关,1=每抽必中)", "老虎机"),
        "slot.winSuppressProb":           (0, "强制失败概率(负面,建议0)", "老虎机"),
        "slot.symbolWeights.W":           (0.2, "战士符号权重", "老虎机"),
        "slot.symbolWeights.M":           (0.15, "法师符号权重", "老虎机"),
        "slot.symbolWeights.D":           (0.1, "决斗者符号权重", "老虎机"),
        "slot.symbolWeights.H":           (0.15, "骑兵符号权重", "老虎机"),
        "slot.symbolWeights.S":           (0.2, "枪兵符号权重", "老虎机"),
        "slot.symbolWeights.A":           (0.2, "弓手符号权重", "老虎机"),
        # ---- 稀有度 ----
        "unitGrades.probabilities.normal":    (1, "普通单位权重", "稀有度"),
        "unitGrades.probabilities.advanced":  (0, "高级单位权重(0=未解锁)", "稀有度"),
        "unitGrades.probabilities.rare":      (0, "稀有单位权重", "稀有度"),
        "unitGrades.probabilities.legendary": (0, "传说单位权重", "稀有度"),
        "unitGrades.probabilities.prism":     (0, "棱镜单位权重", "稀有度"),
        "unitGrades.probabilities.special":   (0, "特殊单位权重", "稀有度"),
        # ---- 经济 ----
        "economy.spins.freePerWave":      (3, "每波免费抽奖次数", "经济"),
        "economy.spins.paidMaxPerWave":   (3, "每波付费抽奖上限", "经济"),
        "economy.spins.paidStartCost":    (100, "首次付费抽奖花费", "经济"),
        "economy.spins.paidCostStep":     (50, "付费抽奖每次涨价", "经济"),
        "economy.spins.refundOnFail":     (0, "未中奖返还金币数", "经济"),
        "economy.wave.baseClear":         (100, "清波基础金币", "经济"),
        "economy.wave.fastClearBonusMax": (20, "快速清波奖励上限", "经济"),
        "economy.wave.survivalBonusMax":  (10, "存活奖励上限", "经济"),
        "economy.wave.condolenceOnFail":  (10, "失败安慰金", "经济"),
        # ---- 战力基线 ----
        "power.baseline.ATK0":            (9, "战力计算攻击基准", "战力计算"),
        "power.baseline.HP0":             (70, "战力计算生命基准", "战力计算"),
        "power.baseline.ASPD0":           (1.5, "战力计算攻速基准", "战力计算"),
        "evolution.absorbGrowthEfficiency[1].ratio": (1, "前30级攻击生命成长效率", "吸收成长"),
        "evolution.absorbGrowthEfficiency[2].ratio": (.5, "30级后攻击生命成长效率", "吸收成长"),
        "evolution.absorbAspdEfficiency[1].ratio": (1, "前30级攻速成长效率", "吸收成长"),
        "evolution.absorbAspdEfficiency[2].ratio": (.5, "31至200级攻速成长效率", "吸收成长"),
        "evolution.absorbAspdEfficiency[3].ratio": (.1, "200级后攻速成长效率", "吸收成长"),
        "power.absorb.atkPerPower":       (0.09, "吸收:每点战力加攻", "战力"),
        "power.absorb.hpPerPower":        (0.3, "吸收:每点战力加血", "战力"),
        "power.absorb.aspdPerPower":     (0.035, "吸收:每点战力加攻速", "战力"),
    },
}

# 局外存档预设(勾选即应用的 lua 语句)
SAVE_PRESETS = [
    ("originite", "局外货币 源质数量(输入框设值)", ["s.originite = {originite}"]),
    ("unlock_stages", "解锁全部关卡",
     ["for k in pairs(s.unlockedStages or {}) do s.unlockedStages[k] = true end"]),
    ("unlock_diff", "解锁全部难度",
     ["for st, list in pairs(s.unlockedDifficulties or {}) do "
      "for d in pairs(list) do list[d] = true end end"]),
    ("clear_diff", "标记全部难度已通关",
     ["for st, list in pairs(s.clearedDifficulties or {}) do "
      "for d in pairs(list) do list[d] = true end end"]),
    ("stats", "成就统计:局数/BOSS/时长(输入框设值)",
     ["s.achievements.runs = {runs}", "s.achievements.bosses = {bosses}",
      "s.achievements.played = {played}"]),
]
