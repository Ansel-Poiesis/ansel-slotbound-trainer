"""Slotbound feature composition and a reversible, single-worker runtime session."""

from dataclasses import dataclass
import math
import queue
import threading
import time

from runtime_patch import GameMemory, PTR_MASK, TAB_TAG
from scanner import ValueScanner
from sbmod_params import CONSTANTS_EDITS
from symbol_assets import SYMBOL_NAMES


VERSION = "4.4.0"
SYMBOLS = SYMBOL_NAMES
GRADES = {"普通": "normal", "高级": "advanced", "稀有": "rare",
          "传说": "legendary", "棱镜": "prism"}
GRADE_PRESETS = {name: dict(zip(GRADES.values(), percentages)) for name, percentages in (
    ("全部传说", (0, 0, 0, 100, 0)),
    ("传说优先", (10, 10, 20, 50, 10)),
    ("稀有优先", (20, 20, 40, 15, 5)),
    ("平均分配", (20, 20, 20, 20, 20)),
    ("全部棱镜", (0, 0, 0, 0, 100)),
    ("全部普通", (100, 0, 0, 0, 0)),
)}
GRADE_PATHS = {"unitGrades.probabilities." + grade for grade in GRADES.values()}
MODES = ("每抽必中", "提高中奖", "连败保底", "同符号满盘")
LEGACY_CONSTANT_PATHS = {"unitGrades.probabilities.special", "economy.spins.freePerWave",
                         "economy.spins.paidMaxPerWave", "economy.spins.paidStartCost",
                         "economy.spins.paidCostStep", "power.absorb.atkPerPower",
                         "power.absorb.hpPerPower", "power.absorb.aspdPerPower"}
PARAMETERS = {path: item for path, item in CONSTANTS_EDITS["src/data/constants.lua"].items()
              if path not in LEGACY_CONSTANT_PATHS}


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    timing: str
    default: float = 1
    minimum: float = 0
    maximum: float = 1000
    step: float = 1
    integer: bool = False


FEATURES = (
    Feature("draw", "持续抽奖辅助", "下一次抽奖"),
    Feature("weights", "自定义符号权重", "下一次抽奖"),
    Feature("free_wave", "每波免费旋转", "本波及以后每波补充", 10, 0, 999, 1, True),
    Feature("paid_free", "付费抽奖免金币", "当前价格与后续抽奖"),
    Feature("paid_max", "每波旋转总数", "包含免费次数 · 本波及以后每波", 12, 0, 999, 1, True),
    Feature("refund", "未中奖返还金币", "原生失败结算 · 固定金币数", 100, 0, 999999, 50, True),
    Feature("reward", "清波基础奖励倍率", "含固定 400 及三项基础奖励", 2, 1, 100, .5),
    Feature("grade", "抽卡品质概率", "下一次生成新单位 · 按下方百分比分配"),
    Feature("attack", "部队攻击倍率", "备战部队 · 下次出战生效", 2, .1, 100, .5),
    Feature("health", "部队生命倍率", "备战部队 · 下次出战生效", 2, .1, 100, .5),
    Feature("speed", "部队攻速倍率", "备战部队 · 下次出战生效", 2, .1, 20, .25),
    Feature("absorb", "吸收成长倍率", "下一次吸收", 2, 0, 100, .5),
    Feature("battle_speed", "战斗速度", "3 倍限无尽模式且需解锁", 2, 2, 3, 1, True),
)
FEATURE_BY_KEY = {item.key: item for item in FEATURES}
DEFAULT_VALUES = {item.key: item.default for item in FEATURES}
DEFAULT_VALUES.update(draw_mode="每抽必中", chance=75, pity=3, symbol="W")
DEFAULT_VALUES.update({"grade_" + grade: percent for grade, percent in GRADE_PRESETS["全部传说"].items()})
DEFAULT_WEIGHTS = {"W": .2, "A": .2, "S": .2, "M": .15, "D": .1, "H": .15}
RESOURCE_PATHS = {"coins": "coins", "spins": "spinsLeft", "credits": "freeSpinCredits",
                  "base_hp": "battlefield.baseHP"}


def number(value, minimum=0, maximum=1e9, integer=False):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("请输入数字") from None
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError("请输入 %g 至 %g 之间的数值" % (minimum, maximum))
    if integer and not parsed.is_integer():
        raise ValueError("请输入整数")
    return int(parsed) if integer else parsed


def validate_parameter(path, value):
    if path not in PARAMETERS:
        raise ValueError("未知参数 " + path)
    minimum, maximum = (-1e6, 1e6) if path == "hiddenLuck.debtMin" else (0, 1e6)
    if ".chances[" in path or path in {"slot.forceWinProb", "slot.winSuppressProb",
                                      "hiddenLuck.naturalWinProbability"}:
        minimum, maximum = 0, 1
    integral = path.endswith(("certainAt", "minWave", "fromSpin", "freePerWave", "paidMaxPerWave"))
    return number(value, minimum, maximum, integral)


def grade_percentages(values):
    return {grade: number(values.get("grade_" + grade, 0), 0, 100) for grade in GRADES.values()}


def grade_distribution(values):
    percentages = grade_percentages(values)
    total = math.fsum(percentages.values())
    if not math.isclose(total, 100, rel_tol=0, abs_tol=1e-7):
        raise ValueError("品质概率合计为 %g%%，请调整至 100%%，或点击按比例归一" % total)
    return {grade: percent / 100 for grade, percent in percentages.items()}


def normalize_grade_percentages(values):
    percentages = grade_percentages(values)
    total = math.fsum(percentages.values())
    if total <= 0:
        raise ValueError("至少一档品质概率需要大于 0")
    result = {grade: round(percent / total * 100, 2) for grade, percent in percentages.items()}
    largest = max(percentages, key=percentages.get)
    result[largest] = round(result[largest] + 100 - math.fsum(result.values()), 2)
    return result


def compose_edits(enabled, values, weights, custom, baseline, in_run=False):
    """Compose a complete desired state. Shared fields have deterministic owners."""
    result = {}

    def put(path, value, root="constants"):
        result[(root, path)] = value

    for key in enabled:
        if key not in FEATURE_BY_KEY:
            raise ValueError("未知功能 " + key)
        spec = FEATURE_BY_KEY[key]
        if key not in {"draw", "weights", "grade", "paid_free"}:
            number(values[key], spec.minimum, spec.maximum, spec.integer)
    if "weights" in enabled:
        current = {symbol: number(weights[symbol], 0, 1e6) for symbol in SYMBOLS}
        if sum(current.values()) <= 0:
            raise ValueError("符号权重总和需要大于零")
        for symbol, value in current.items():
            put("slot.symbolWeights." + symbol, value)
    if "draw" in enabled:
        mode = values["draw_mode"]
        if mode not in MODES:
            raise ValueError("请选择抽奖模式")
        if mode == "连败保底":
            put("hiddenLuck.streak.certainAt", number(values["pity"], 1, 100, True))
        else:
            probability = number(values["chance"], 0, 100) / 100 if mode == "提高中奖" else 1
            put("slot.forceWinProb", probability)
            put("slot.winSuppressProb", 0)
        if mode == "同符号满盘":
            if values["symbol"] not in SYMBOLS:
                raise ValueError("请选择符号")
            for symbol in SYMBOLS:
                put("slot.symbolWeights." + symbol, int(symbol == values["symbol"]))
    if "paid_free" in enabled:
        if in_run:
            put("spinCost", 0, "globals")
            put("spinCostStep", 0, "globals")
    if "refund" in enabled:
        put("economy.spins.refundOnFail", values["refund"])
    multipliers = {"reward": ("economy.wave.baseClear", "economy.wave.fastClearBonusMax",
                              "economy.wave.survivalBonusMax"),
                   "absorb": ("evolution.absorbGrowthEfficiency[1].ratio",
                              "evolution.absorbGrowthEfficiency[2].ratio",
                              "evolution.absorbAspdEfficiency[1].ratio",
                              "evolution.absorbAspdEfficiency[2].ratio",
                              "evolution.absorbAspdEfficiency[3].ratio")}
    for feature, paths in multipliers.items():
        if feature in enabled:
            for path in paths:
                put(path, baseline(("constants", path)) * values[feature])
    if "reward" in enabled:
        # 1.0.0.7 adds a literal 400 after its configurable victory rewards.
        # Include that base payment in the requested multiplier through the
        # victory-only baseClear term. Native upgrades/endless bonuses stay native.
        result[("constants", "economy.wave.baseClear")] += 400 * (values["reward"] - 1)
    if "battle_speed" in enabled and in_run:
        put("battlefield.battleFastForward", True, "globals")
        put("battlefield.battleFastForwardLevel", number(values["battle_speed"], 2, 3, True), "globals")
    for path, value in custom.items():
        put(path, validate_parameter(path, value))
    if "grade" in enabled:
        # The complete distribution owns these fields while active; an old
        # advanced override cannot silently change a displayed percentage.
        for grade, probability in grade_distribution(values).items():
            put("unitGrades.probabilities." + grade, probability)
        put("unitGrades.probabilities.special", 0)
    return result


@dataclass
class Patch:
    address: int
    original: float | bool | None
    target: float | bool | None


def same_value(left, right):
    if isinstance(left, bool) or isinstance(right, bool) or left is None or right is None:
        return type(left) is type(right) and left == right
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


class PatchSession:
    def __init__(self, memory):
        self.memory = memory
        self.patches = {}

    def baseline(self, key):
        root, path = key
        address = self.memory.resolve_path(path, root)
        value = self.memory.read_d(address)
        if value is None:
            raise RuntimeError("参数类型不匹配 " + path)
        patch = self.patches.get(key)
        if patch and patch.address == address and same_value(value, patch.target):
            return patch.original
        return value

    def sync(self, desired):
        """Plan all writes first, then verify every write and rollback a failed batch."""
        next_patches = {}
        writes = []
        for key in sorted(self.patches.keys() | desired.keys()):
            root, path = key
            try:
                address = self.memory.resolve_path(path, root)
            except RuntimeError:
                if key not in desired and root == "globals":
                    continue
                raise
            scalar = root == "globals" and path in {
                "battlefield.battleFastForward", "battlefield.battleFastForwardLevel"}
            current = self.memory.read_scalar(address) if scalar else self.memory.read_d(address)
            if current is None and not scalar:
                if key not in desired and root == "globals":
                    continue
                raise RuntimeError("参数当前为非数值 " + path)
            old_patch = self.patches.get(key)
            if key in desired:
                target = desired[key] if scalar else number(desired[key], -1e12, 1e12)
                original = current
                if old_patch and old_patch.address == address and same_value(current, old_patch.target):
                    original = old_patch.original
                next_patches[key] = Patch(address, original, target)
            else:
                # An object replaced by the game owns its freshly computed value.
                if old_patch.address != address:
                    continue
                target = old_patch.original
                if not same_value(current, old_patch.target):
                    continue
            if not same_value(current, target):
                writes.append((address, current, target))
        completed = []
        try:
            for address, current, target in writes:
                completed.append((address, current))
                self.memory.write_scalar(address, target)
        except Exception as error:
            failures = []
            for address, original in reversed(completed):
                try:
                    self.memory.write_scalar(address, original)
                except Exception as rollback_error:
                    failures.append(str(rollback_error))
            suffix = "，恢复失败 " + " / ".join(failures) if failures else "，本次写入已恢复"
            raise RuntimeError(str(error) + suffix) from error
        self.patches = next_patches
        return len(writes)

    def discard_globals(self):
        retained = {key: patch.target for key, patch in self.patches.items() if key[0] != "globals"}
        self.sync(retained)


@dataclass
class UnitStatPatch:
    address: int
    original: float
    target: float
    factor: float


class UnitStatSession:
    """Scale actual roster stats and carry native growth without compounding."""
    FIELDS = {"attack": "atk", "health": "hp", "speed": "aspd"}

    def __init__(self, memory):
        self.memory = memory
        self.patches = {}
        self.unit_count = 0

    def sync(self, enabled, values):
        indices = self.memory.table_array_indices("battlefield.rosterUnits", "globals")
        self.unit_count = len(indices)
        # Native absorption removes array entries and shifts surviving units.
        # Ownership follows the live stat address, never the old roster index.
        previous_by_address = {patch.address: patch for patch in self.patches.values()}
        next_patches, writes = {}, []
        for index in indices:
            for feature, field in self.FIELDS.items():
                if feature not in enabled and not previous_by_address:
                    continue
                path = "battlefield.rosterUnits[%d].stats.%s" % (index, field)
                try:
                    address = self.memory.resolve_path(path, "globals")
                except RuntimeError:
                    if feature in enabled:
                        raise
                    continue
                previous = previous_by_address.get(address)
                if feature not in enabled and not previous:
                    continue
                current = self.memory.read_d(address)
                if current is None:
                    raise RuntimeError("单位属性当前不是数值 " + path)
                base = current
                if previous:
                    base = previous.original if same_value(current, previous.target) else current / previous.factor
                factor = number(values[feature], .1, 100) if feature in enabled else 1
                target = base * factor
                if feature in enabled:
                    next_patches[path] = UnitStatPatch(address, base, target, factor)
                if not same_value(current, target):
                    writes.append((address, current, target))
        completed = []
        try:
            for address, current, target in writes:
                completed.append((address, current))
                self.memory.write_number(address, target)
        except Exception as error:
            failures = []
            for address, original in reversed(completed):
                try:
                    self.memory.write_number(address, original)
                except Exception as rollback_error:
                    failures.append(str(rollback_error))
            suffix = "，恢复失败 " + " / ".join(failures) if failures else "，本次写入已恢复"
            raise RuntimeError(str(error) + suffix) from error
        self.patches = next_patches
        return len(writes)


class RuntimeWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="slotbound-runtime", daemon=True)
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.stop_requested = threading.Event()
        self.memory = None
        self.session = None
        self.scanner = None
        self.scan_locks = {}
        self.enabled = set()
        self.values = dict(DEFAULT_VALUES)
        self.weights = dict(DEFAULT_WEIGHTS)
        self.custom = {}
        self.keep_across_runs = True
        self.run_token = None
        self.in_run = False
        self.pending_win = None
        self.draw_marker = None
        self.last_applied = None
        self.show_parameters = False
        self._last_state = None
        self.unit_session = None
        self.wave_config = None
        self.game_state = {}

    def submit(self, action, **data):
        self.commands.put((action, data))

    def emit(self, event, **data):
        self.events.put((event, data))

    def connect(self):
        if self.memory and self.memory.alive():
            return
        if self.memory:
            self.memory.close()
        memory = GameMemory()
        try:
            memory.root_table()
        except Exception:
            memory.close()
            raise
        self.memory = memory
        self.session = PatchSession(self.memory)
        self.unit_session = UnitStatSession(self.memory)
        self.wave_config = None
        self.scanner = ValueScanner(self.memory)
        self.scan_locks.clear()
        self.run_token = None
        self.pending_win = None
        self.draw_marker = None
        self.last_applied = None
        self.emit("connection", pid=self.memory.pid, text="已连接游戏")

    def current_state(self):
        gm = self.memory
        previous_phase = self.game_state.get("phase")
        state = {}
        for name, path in RESOURCE_PATHS.items():
            try:
                state[name] = gm.read_d(gm.resolve_path(path, "globals"))
            except RuntimeError:
                state[name] = None
        state["phase"] = ""
        game_over = False
        try:
            state["phase"] = gm.read_string_path("phase")
            pointer = gm.read_q(gm.resolve_path("battlefield", "globals"))
            token = pointer & PTR_MASK if pointer and pointer >> 47 == TAB_TAG >> 47 else None
            wave_id = gm.read_path("battlefield.wave.id", "globals")
            active = gm.read_boolean(gm.resolve_path("battlefield.wave.active", "globals")) is True
            state["wave"] = max(1, wave_id if active else wave_id + 1)
        except RuntimeError:
            token = None
            state["wave"] = None
            game_over = False
        try:
            game_over = gm.read_boolean(gm.resolve_path("battlefield.gameOver", "globals")) is True
        except RuntimeError:
            pass
        self.in_run = bool(state["coins"] is not None and state["wave"] is not None
                           and state["wave"] >= 1 and state["phase"] in {"spin", "spinning", "battle"}
                           and not game_over)
        state["in_run"] = self.in_run
        state["free_spins"] = min(max(0, state.get("spins") or 0),
                                 max(0, state.get("credits") or 0))
        if previous_phase in {"spin", "spinning", "battle"} and state["phase"] == "menu":
            self.scan_locks.clear()
            self.scanner.reset()
            if not self.keep_across_runs:
                self.enabled.clear()
                self.custom.clear()
                self.emit("cleared", text="已结束本局覆盖")
        if self.run_token and token and token != self.run_token:
            self.session.discard_globals()
            if self.unit_session:
                self.unit_session.sync(set(), self.values)
            self.wave_config = None
            self.scan_locks.clear()
            self.scanner.reset()
            self.pending_win = None
            self.draw_marker = None
            if not self.keep_across_runs:
                self.enabled.clear()
                self.custom.clear()
                self.emit("cleared", text="新局已关闭覆盖")
            self.emit("log", text="当前局对象已变化，已重新建立定位")
        self.run_token = token
        self.game_state = state
        return state

    def apply(self):
        desired = compose_edits(self.enabled, self.values, self.weights, self.custom,
                                self.session.baseline, self.in_run)
        speed_level = ("globals", "battlefield.battleFastForwardLevel")
        if speed_level in desired:
            try:
                self.memory.resolve_path(speed_level[1], speed_level[0])
            except RuntimeError:
                if desired[speed_level] != 2:
                    raise RuntimeError("请先在游戏中点一次加速按钮，再设为 3 倍") from None
                # The native game defaults to 2x when the level key has never
                # been created. Enabling its existing Boolean flag is sufficient.
                desired.pop(speed_level)
        changed = self.session.sync(desired)
        if self.unit_session is None:
            self.unit_session = UnitStatSession(self.memory)
        try:
            changed += self.unit_session.sync(self.enabled if self.in_run else set(), self.values)
        except Exception as error:
            raise RuntimeError("部分功能已处理；部队属性未完成：" + str(error)) from error
        try:
            changed += self.sync_wave_budget()
        except Exception as error:
            raise RuntimeError("常量和部队属性已处理；本波补充未完成：" + str(error)) from error
        self.sync_draw_marker()
        signature = (frozenset(self.enabled), tuple(sorted(desired.items())), self.in_run,
                     self.unit_session.unit_count)
        if changed or signature != self.last_applied:
            self.emit("applied", enabled=list(self.enabled), custom=dict(self.custom),
                      count=len(desired) + len(self.unit_session.patches), changed=changed,
                      in_run=self.in_run, unit_count=self.unit_session.unit_count)
            self.last_applied = signature

    def sync_wave_budget(self):
        active = self.enabled & {"free_wave", "paid_max"}
        if not active:
            self.wave_config = None
            return 0
        if not self.in_run or self.game_state.get("phase") != "spin":
            return 0
        signature = (self.run_token, self.game_state["wave"],
                     tuple((key, self.values[key]) for key in sorted(active)))
        if signature == self.wave_config:
            return 0
        total = self.memory.read_path("spinsLeft", "globals")
        free = self.memory.read_path("freeSpinCredits", "globals")
        if "paid_max" in active:
            total = number(self.values["paid_max"], 0, 999, True)
        if "free_wave" in active:
            free = number(self.values["free_wave"], 0, 999, True)
            if "paid_max" not in active:
                total = max(total, free)
        free = min(total, free)
        changed = PatchSession(self.memory).sync({("globals", "spinsLeft"): total,
                                                  ("globals", "freeSpinCredits"): free})
        self.wave_config = signature
        self.emit("log", text="第 %g 波补充：总旋转 %g，免费 %g" % (self.game_state["wave"], total, free))
        return changed

    def sync_draw_marker(self):
        active = (self.in_run and "draw" in self.enabled
                  and self.values["draw_mode"] in {"每抽必中", "同符号满盘"})
        if not active and not self.draw_marker:
            return
        try:
            address = self.memory.resolve_path("slot.forceWinOnce", "globals")
        except RuntimeError:
            if active:
                raise
            self.draw_marker = None
            return
        current = self.memory.read_boolean(address)
        if current is None:
            raise RuntimeError("抽奖标记类型不匹配")
        if active:
            if not self.draw_marker or self.draw_marker[0] != address:
                original = current
                if self.pending_win and self.pending_win[0] == address:
                    original = self.pending_win[1]
                    self.pending_win = None
                self.draw_marker = (address, original)
            if not current:
                self.memory.write_boolean(address, True)
        else:
            previous_address, original = self.draw_marker
            if address == previous_address and current:
                self.memory.write_boolean(address, original)
            self.draw_marker = None

    def handle(self, action, data):
        if action.startswith("save_"):
            self.handle_save(action, data)
            return
        if action == "configure":
            if "grade" in data["enabled"]:
                grade_distribution(data["values"])
            self.enabled = set(data["enabled"])
            self.values = dict(data["values"])
            self.weights = dict(data["weights"])
            self.custom = dict(data["custom"])
            self.keep_across_runs = data["keep_across_runs"]
            self.connect()
            self.current_state()
            self.apply()
            return
        if action == "show_parameters":
            self.show_parameters = data["show"]
            return
        if action == "restore":
            self.enabled.clear()
            self.custom.clear()
            self.scan_locks.clear()
            self.wave_config = None
            if self.memory and self.memory.alive():
                self.session.sync({})
                if self.unit_session:
                    self.unit_session.sync(set(), self.values)
                self.sync_draw_marker()
                self.cancel_win()
            self.emit("cleared", text="已关闭功能并恢复覆盖值")
            return
        self.connect()
        self.current_state()
        if action in {"resource", "once_win", "quick_supply"} and not self.in_run:
            raise RuntimeError("请先进入一局游戏")
        if action == "quick_supply":
            grants = {"coins": 10000, "spinsLeft": 20, "freeSpinCredits": 20}
            desired = {("globals", path): number(self.memory.read_path(path, "globals") + amount, 0, 1e9, path != "coins")
                       for path, amount in grants.items()}
            PatchSession(self.memory).sync(desired)
            self.emit("log", text="快捷补给：金币 +10000、免费旋转 +20，三项回读一致")
        elif action == "resource":
            path = RESOURCE_PATHS[data["resource"]]
            address = self.memory.resolve_path(path, "globals")
            old = self.memory.read_d(address)
            if old is None:
                raise RuntimeError("资源当前为非数值")
            amount = number(data["value"], 0, 1e9, True)
            target = number(old + amount if data["operation"] == "add" else amount, 0, 1e9)
            if data["resource"] in {"spins", "credits"}:
                # Native free-spin rewards add both the total rotation budget and
                # the free allowance. A spin consumes both; these are not additive.
                spins = self.memory.read_path("spinsLeft", "globals")
                credits = self.memory.read_path("freeSpinCredits", "globals")
                adding = data["operation"] == "add"
                total = number(spins + amount if adding else amount, 0, 1e9, True)
                free = number(credits + amount if adding else amount, 0, 1e9, True)
                PatchSession(self.memory).sync({("globals", "spinsLeft"): total,
                                                ("globals", "freeSpinCredits"): free})
                self.emit("log", text="剩余旋转 %g → %g，免费额度 %g → %g，回读一致" %
                          (spins, total, credits, free))
            else:
                self.memory.write_number(address, target)
                self.emit("log", text="%s  %g → %g，回读一致" % (path, old, target))
        elif action == "once_win":
            if self.draw_marker:
                self.emit("once", text="持续必中已开启，下一抽按当前模式执行")
                return
            if self.pending_win:
                raise RuntimeError("下一抽必中已在待命，可使用取消按钮撤回")
            address = self.memory.resolve_path("slot.forceWinOnce", "globals")
            original = self.memory.read_boolean(address)
            if original is None:
                raise RuntimeError("下一抽标记类型不匹配")
            self.memory.write_boolean(address, True)
            self.pending_win = (address, original)
            self.emit("once", text="下一抽必中已待命")
        elif action == "cancel_win":
            self.cancel_win()
        elif action == "scan_first":
            self.scan_locks.clear()
            count = self.scanner.first_scan(number(data["value"], -1e12, 1e12))
            self.scan_result(count)
        elif action == "scan_next":
            count = self.scanner.next_scan(number(data["value"], -1e12, 1e12))
            valid = set(self.scanner.candidates)
            self.scan_locks = {a: v for a, v in self.scan_locks.items() if a in valid}
            self.scan_result(count)
        elif action == "scan_write":
            address = data["address"]
            if address not in self.scanner.candidates:
                raise RuntimeError("请先选择本次扫描的候选地址")
            value = number(data["value"], -1e12, 1e12)
            self.memory.write_number(address, value)
            if data["lock"]:
                self.scan_locks[address] = value
            else:
                self.scan_locks.pop(address, None)
            self.emit("log", text="所选地址已写入并回读" + ("，持续锁定" if data["lock"] else ""))
            self.scan_result(len(self.scanner.candidates))
        elif action == "scan_unlock":
            self.scan_locks.clear()
            self.emit("log", text="已停止扫描地址锁定")
        elif action == "reconnect":
            if self.session.patches:
                self.session.sync({})
            if self.unit_session:
                self.unit_session.sync(set(), self.values)
            self.memory.close()
            self.memory = None
            self.connect()
            self.apply()

    def cancel_win(self):
        if self.pending_win:
            address, original = self.pending_win
            current_address = self.memory.resolve_path("slot.forceWinOnce", "globals")
            if address == current_address and self.memory.read_boolean(address) is True:
                self.memory.write_boolean(address, original)
        self.pending_win = None
        self.emit("once", text="单次操作就绪")

    def scan_result(self, count):
        self.emit("scan", count=count, rows=[(address, self.memory.read_d(address))
                                           for address in self.scanner.candidates[:150]],
                  locks=list(self.scan_locks))

    def handle_save(self, action, data):
        import os
        import shutil
        import save_backend as core
        from sbmod_params import SAVE_PRESETS

        if action != "save_read" and core.game_running():
            raise RuntimeError("关闭游戏后可修改局外存档")
        if action == "save_apply":
            definitions = {key: statements for key, _, statements in SAVE_PRESETS}
            key = data["key"]
            if key not in definitions:
                raise ValueError("未知存档操作")
            values = {name: number(value, 0, 1e9, True) for name, value in data["values"].items()}
            statements = [statement.format(**values) for statement in definitions[key]]
            core.edit_save(statements)
            self.emit("log", text="存档已写入，备份保存在 " + core.SAVE_BAK)
        elif action == "save_restore":
            if not os.path.exists(core.SAVE_BAK):
                raise RuntimeError("尚无修改前存档备份")
            temporary = core.SAVE + ".restore-tmp"
            shutil.copyfile(core.SAVE_BAK, temporary)
            os.replace(temporary, core.SAVE)
            self.emit("log", text="已恢复首次修改前存档")
        expressions = {"originite": "s.originite or 0",
                       "runs": "s.achievements and s.achievements.runs or 0",
                       "bosses": "s.achievements and s.achievements.bosses or 0",
                       "played": "s.achievements and s.achievements.played or 0"}
        values = {name: core.read_save_field(expr) for name, expr in expressions.items()}
        self.emit("save", values=values, path=core.SAVE, backup=core.SAVE_BAK)

    def tick(self):
        self.connect()
        state = self.current_state()
        self.apply()
        state = self.current_state()
        for address, value in list(self.scan_locks.items()):
            if self.memory.read_d(address) is None:
                self.scan_locks.pop(address)
                self.emit("log", text="扫描目标已失效，已停止该地址锁定")
            else:
                self.memory.write_number(address, value)
        if self.pending_win:
            address, _ = self.pending_win
            if self.memory.read_boolean(address) is False:
                self.pending_win = None
                self.emit("once", text="下一抽必中标记已被游戏消耗")
        self.emit("state", **state)
        if self.show_parameters:
            rows = {}
            for path in PARAMETERS:
                try:
                    rows[path] = self.memory.read_d(self.memory.resolve_path(path))
                except RuntimeError:
                    rows[path] = None
            self.emit("parameters", rows=rows)

    def run(self):
        next_tick = 0
        try:
            while not self.stop_requested.is_set():
                try:
                    action, data = self.commands.get(timeout=.1)
                except queue.Empty:
                    action, data = None, None
                try:
                    if action:
                        self.handle(action, data)
                    if time.monotonic() >= next_tick:
                        next_tick = time.monotonic() + .5
                        self.tick()
                    self._last_state = None
                except Exception as error:
                    message = str(error)
                    if message != self._last_state or action:
                        self.emit("error", text=message, action=action)
                        self._last_state = message
                    if self.memory and not self.memory.alive():
                        self.memory.close()
                        self.memory = None
                        self.session = None
                        self.scan_locks.clear()
                        self.pending_win = None
                        self.draw_marker = None
        finally:
            if self.memory:
                try:
                    if self.memory.alive() and self.session:
                        self.session.sync({})
                        if self.unit_session:
                            self.unit_session.sync(set(), self.values)
                        self.enabled.clear()
                        self.sync_draw_marker()
                        self.cancel_win()
                except Exception as error:
                    self.emit("error", text="退出恢复需要检查 " + str(error), action="close")
                self.memory.close()
