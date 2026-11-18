"""Synthetic fixtures; optional user-provided LuaJIT DLL, never game source or bytecode."""

import ctypes
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import save_backend as core
from game_paths import find_game_directory
from test_fixtures import constants_source
from runtime_patch import GameMemory, apply_runtime, build_patch_ops
from scanner import ValueScanner
from trainer_engine import (DEFAULT_VALUES, DEFAULT_WEIGHTS, FEATURES, PARAMETERS, VERSION,
                            GRADES, GRADE_PRESETS, PatchSession, RuntimeWorker, compose_edits, number,
                            grade_distribution, normalize_grade_percentages)


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if find_game_directory() is None:
            raise unittest.SkipTest("Optional: provide SLOTBOUND_GAME_DIR for isolated LuaJIT integration")
        cls.lua = core.LuaJIT()
        cls.lib = cls.lua._l
        cls.lib.lua_topointer.argtypes = [ctypes.c_void_p, ctypes.c_int]
        cls.lib.lua_topointer.restype = ctypes.c_void_p
        cls.lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_size_t, ctypes.c_char_p]
        cls.lib.lua_setfield.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        cls.bytecode = constants_source()

    @classmethod
    def tearDownClass(cls):
        cls.lua.close()

    def table(self, script, global_name):
        encoded = script.encode("utf-8") if isinstance(script, str) else script
        self.assertEqual(self.lib.luaL_loadbuffer(self.lua.L, encoded, len(encoded), b"fixture"), 0)
        self.assertEqual(self.lib.lua_pcall(self.lua.L, 0, 1, 0), 0)
        pointer = self.lib.lua_topointer(self.lua.L, -1)
        self.lib.lua_setfield(self.lua.L, -10002, global_name.encode())
        return pointer

    def setUp(self):
        self.memory = GameMemory(os.getpid())
        constants = self.table(self.bytecode, "__trainer_constants")
        globals_table = self.table("""
            return {math={},love={},package={},coins=100,spinsLeft=3,
                    freeSpinCredits=0,spinCost=100,spinCostStep=50,
                    phase='spin',slot={forceWinOnce=false},
                    battlefield={wave={id=0,active=false},gameOver=false,baseHP=100,
                        rosterUnits={{stats={atk=9,hp=70,aspd=1.5}}},
                        battleFastForward=false,battleFastForwardLevel=1}}
        """, "__trainer_globals")
        self.memory._roots = {"constants": constants, "globals": globals_table}
        self.session = PatchSession(self.memory)
        self.values = dict(DEFAULT_VALUES)
        self.weights = dict(DEFAULT_WEIGHTS)
        self.worker = RuntimeWorker()
        self.worker.memory = self.memory
        self.worker.session = self.session
        self.worker.scanner = ValueScanner(self.memory)

    def tearDown(self):
        self.memory.close()

    def read(self, path, root="constants"):
        return self.memory.read_d(self.memory.resolve_path(path, root))

    def edits(self, enabled=(), custom=None, in_run=True):
        return compose_edits(set(enabled), self.values, self.weights, custom or {},
                             self.session.baseline, in_run)

    def test_all_default_parameter_paths_resolve(self):
        self.assertEqual(len(PARAMETERS), 48)
        for path, (original, description, group) in PARAMETERS.items():
            with self.subTest(path=path):
                self.assertAlmostEqual(self.read(path), original)
                self.assertEqual(len(build_patch_ops({path: original})), 1)

    def test_gc64_lua_array_index_is_lua_index(self):
        self.assertAlmostEqual(self.read("hiddenLuck.streak.chances[4]"), .15)
        self.assertAlmostEqual(self.read("hiddenLuck.early[2].chances[3]"), .25)
        self.assertAlmostEqual(self.read("hiddenLuck.round.chances[2]"), .7)
        self.session.sync({("constants", "hiddenLuck.streak.chances[4]"): .9})
        self.assertAlmostEqual(self.read("hiddenLuck.streak.chances[4]"), .9)
        self.assertAlmostEqual(self.read("hiddenLuck.streak.chances[5]"), .3)
        self.session.sync({})
        self.assertAlmostEqual(self.read("hiddenLuck.streak.chances[4]"), .15)

    def test_legacy_apply_uses_full_path_and_readback(self):
        edits = {"evolution.absorbAspdEfficiency[1].ratio": .7, "slot.forceWinProb": .8}
        self.assertTrue(apply_runtime(self.memory, build_patch_ops(edits), log=lambda line: None))
        self.assertAlmostEqual(self.read("evolution.absorbAspdEfficiency[1].ratio"), .7)
        self.assertAlmostEqual(self.read("slot.forceWinProb"), .8)
        self.assertEqual(len(self.memory._patch_addrs), 2)

    def test_all_off_does_not_write_symbol_weights(self):
        self.assertEqual(self.edits(), {})
        self.session.sync({})
        self.assertEqual(self.session.patches, {})
        self.assertAlmostEqual(self.read("slot.symbolWeights.W"), .2)

    def test_zero_is_preserved(self):
        self.worker.values.update(free_wave=0, paid_max=0)
        self.worker.enabled = {"free_wave", "paid_max"}
        self.worker.current_state()
        self.worker.apply()
        self.assertEqual(self.read("spinsLeft", "globals"), 0)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 0)
        self.assertEqual(self.read("economy.spins.freePerWave"), 3)

    def test_pity_eight_keeps_original_chance_curve(self):
        self.values.update(draw_mode="连败保底", pity=8)
        desired = self.edits(["draw"])
        self.assertEqual(desired, {("constants", "hiddenLuck.streak.certainAt"): 8})
        self.session.sync(desired)
        self.assertAlmostEqual(self.read("hiddenLuck.streak.chances[4]"), .15)

    def test_full_grid_has_priority_then_custom_weights_resume(self):
        self.values.update(draw_mode="同符号满盘", symbol="A")
        self.weights["W"] = .6
        self.session.sync(self.edits(["draw", "weights"]))
        self.assertEqual(self.read("slot.symbolWeights.A"), 1)
        self.assertEqual(self.read("slot.symbolWeights.W"), 0)
        self.session.sync(self.edits(["weights"]))
        self.assertAlmostEqual(self.read("slot.symbolWeights.W"), .6)
        self.assertEqual(self.read("slot.forceWinProb"), 0)
        self.session.sync({})
        self.assertAlmostEqual(self.read("slot.symbolWeights.W"), .2)

    def test_multipliers_do_not_compound(self):
        self.worker.current_state()
        self.worker.enabled = {"attack", "absorb", "reward"}
        self.worker.apply()
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.atk", "globals"), 18)
        self.worker.apply()
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.atk", "globals"), 18)
        self.worker.values["attack"] = 3
        self.worker.enabled = {"attack"}
        self.worker.apply()
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.atk", "globals"), 27)
        self.assertAlmostEqual(self.read("evolution.absorbGrowthEfficiency[1].ratio"), 1)
        self.worker.handle("restore", {})
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.atk", "globals"), 9)
        self.assertEqual(self.read("power.baseline.ATK0"), 9)

    def test_every_feature_has_valid_runtime_composition(self):
        for feature in FEATURES:
            with self.subTest(feature=feature.key):
                self.session.sync(self.edits([feature.key]))
                self.session.sync({})
        for path, (original, description, group) in PARAMETERS.items():
            self.assertAlmostEqual(self.read(path), original)

    def test_grade_mix_writes_exact_percentages_and_restores_originals(self):
        percentages = dict(zip(GRADES.values(), (20, 10, 40, 25, 5)))
        self.values.update({"grade_" + grade: percent for grade, percent in percentages.items()})
        self.session.sync(self.edits(["grade"]))
        for grade, percent in percentages.items():
            self.assertAlmostEqual(self.read("unitGrades.probabilities." + grade), percent / 100)
        self.session.sync({})
        for grade in GRADES.values():
            self.assertEqual(self.read("unitGrades.probabilities." + grade), int(grade == "normal"))

    def test_invalid_grade_configuration_preserves_last_applied_configuration(self):
        good = {"enabled": ["grade"], "values": dict(self.values), "weights": dict(self.weights),
                "custom": {}, "keep_across_runs": True}
        self.worker.handle("configure", good)
        invalid = dict(good, values=dict(self.values, grade_legendary=80))
        with self.assertRaisesRegex(ValueError, "100%"):
            self.worker.handle("configure", invalid)
        self.assertEqual(self.worker.values["grade_legendary"], 100)
        self.assertEqual(self.read("unitGrades.probabilities.legendary"), 1)
        self.worker.handle("restore", {})
        self.assertEqual(self.read("unitGrades.probabilities.normal"), 1)

    def test_grade_panel_has_priority_over_advanced_values_until_disabled(self):
        custom = {"unitGrades.probabilities.normal": .1, "unitGrades.probabilities.legendary": .9}
        self.values.update({"grade_" + grade: percent for grade, percent in GRADE_PRESETS["稀有优先"].items()})
        self.session.sync(self.edits(["grade"], custom))
        self.assertAlmostEqual(self.read("unitGrades.probabilities.legendary"), .15)
        self.assertAlmostEqual(self.read("unitGrades.probabilities.rare"), .4)
        self.session.sync(self.edits([], custom))
        self.assertAlmostEqual(self.read("unitGrades.probabilities.legendary"), .9)
        self.assertEqual(self.read("unitGrades.probabilities.rare"), 0)
        self.session.sync({})
        self.assertEqual(self.read("unitGrades.probabilities.normal"), 1)

    def test_partial_failure_rolls_back_and_keeps_applied_state(self):
        real_write = self.memory.write_number
        count = 0

        def fail_second(address, value):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("simulated write failure")
            return real_write(address, value)

        with mock.patch.object(self.memory, "write_number", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "本次写入已恢复"):
                self.session.sync(self.edits(["paid_free"]))
        self.assertEqual(self.read("spinCost", "globals"), 100)
        self.assertEqual(self.read("spinCostStep", "globals"), 50)
        self.assertEqual(self.session.patches, {})

    def test_unit_batch_failure_restores_attributes(self):
        real_write = self.memory.write_number
        def fail_health(address, value):
            if value == 140:
                raise RuntimeError("unit health write failed")
            return real_write(address, value)
        self.worker.current_state()
        self.worker.enabled = {"attack", "health"}
        with mock.patch.object(self.memory, "write_number", side_effect=fail_health):
            with self.assertRaisesRegex(RuntimeError, "本次写入已恢复"):
                self.worker.apply()
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.atk", "globals"), 9)
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.hp", "globals"), 70)
        self.assertEqual(self.worker.unit_session.patches, {})

    def test_mixed_failure_reports_prior_success_and_restore_removes_it(self):
        real_write = self.memory.write_number
        def fail_health(address, value):
            if value == 140:
                raise RuntimeError("unit health write failed")
            return real_write(address, value)
        self.worker.current_state()
        self.worker.enabled = {"reward", "attack", "health"}
        with mock.patch.object(self.memory, "write_number", side_effect=fail_health):
            with self.assertRaisesRegex(RuntimeError, "部分功能已处理；部队属性未完成"):
                self.worker.apply()
        self.assertEqual(self.read("economy.wave.baseClear"), 600)
        self.assertEqual(self.read("battlefield.rosterUnits[1].stats.atk", "globals"), 9)
        self.worker.handle("restore", {})
        self.assertEqual(self.read("economy.wave.baseClear"), 100)

    def test_external_game_recalculation_is_preserved_when_disabled(self):
        self.session.sync(self.edits(["reward"]))
        address = self.memory.resolve_path("economy.wave.baseClear")
        self.memory.write_number(address, 240)
        self.session.sync({})
        self.assertEqual(self.read("economy.wave.baseClear"), 240)

    def test_dynamic_table_replacement_rebinds_current_weights(self):
        self.values.update(draw_mode="同符号满盘", symbol="W")
        self.session.sync(self.edits(["draw"]))
        old_address = self.memory.resolve_path("slot.symbolWeights.W")
        self.lua.dostring("__trainer_constants.slot.symbolWeights={W=.35,A=.2,S=.2,M=.1,D=.05,H=.1}")
        self.session.sync(self.edits(["draw"]))
        self.assertNotEqual(self.memory.resolve_path("slot.symbolWeights.W"), old_address)
        self.assertEqual(self.read("slot.symbolWeights.W"), 1)
        self.session.sync({})
        self.assertAlmostEqual(self.read("slot.symbolWeights.W"), .35)

    def test_new_hash_key_is_detected_without_table_reallocation(self):
        with self.assertRaises(RuntimeError):
            self.memory.resolve_path("newTrainerCounter", "globals")
        self.lua.dostring("__trainer_globals.newTrainerCounter=77")
        self.assertEqual(self.read("newTrainerCounter", "globals"), 77)

    def test_stale_cached_key_address_is_revalidated(self):
        table = self.memory._roots["globals"]
        correct = self.memory.resolve_path("coins", "globals")
        wrong = self.memory.resolve_path("spinsLeft", "globals")
        identity, nodes = self.memory._node_cache[table]
        stale = dict(nodes)
        stale["coins"] = wrong
        self.memory._node_cache[table] = (identity, stale)
        self.assertEqual(self.memory.resolve_path("coins", "globals"), correct)
        self.assertEqual(self.read("coins", "globals"), 100)

    def test_stream_scan_keeps_chunk_boundary_matches_and_separates_regions(self):
        chunks = [(100, b"xxab"), (104, b"cdyyabcd"), (300, b"ab"), (400, b"cd")]
        with mock.patch.object(self.memory, "private_chunks", return_value=iter(chunks)):
            with mock.patch.object(self.memory, "_snapshot", side_effect=AssertionError("whole snapshot forbidden")):
                self.assertEqual(self.memory.scan(b"abcd"), [102, 108])
        raw = __import__("struct").pack("<d", 54321)
        with mock.patch.object(self.memory, "private_chunks", return_value=iter([(500, raw[:3]), (503, raw[3:])])):
            self.assertEqual(self.worker.scanner.first_scan(54321), 1)
            self.assertEqual(self.worker.scanner.candidates, [500])

    def test_boolean_next_win_can_arm_cancel_and_be_consumed(self):
        address = self.memory.resolve_path("slot.forceWinOnce", "globals")
        self.assertIs(self.memory.read_boolean(address), False)
        self.worker.handle("once_win", {})
        self.assertIs(self.memory.read_boolean(address), True)
        self.worker.handle("cancel_win", {})
        self.assertIs(self.memory.read_boolean(address), False)
        self.worker.handle("once_win", {})
        self.memory.write_boolean(address, False)
        self.worker.tick()
        self.assertIsNone(self.worker.pending_win)

    def test_current_resources_and_total_free_spins(self):
        self.worker.handle("resource", {"resource": "coins", "operation": "add", "value": 1000})
        self.assertEqual(self.read("coins", "globals"), 1100)
        self.worker.handle("resource", {"resource": "spins", "operation": "add", "value": 5})
        self.assertEqual(self.read("freeSpinCredits", "globals"), 5)
        self.assertEqual(self.read("spinsLeft", "globals"), 8)
        # Native main.lua consumes both fields per free spin. Credits are a
        # subset of the budget, not extra rotations to add to the displayed sum.
        self.lua.dostring("__trainer_globals.spinsLeft=7; __trainer_globals.freeSpinCredits=4")
        state = self.worker.current_state()
        self.assertEqual(state["spins"], 7)
        self.assertEqual(state["free_spins"], 4)
        self.worker.handle("resource", {"resource": "spins", "operation": "set", "value": 2})
        self.assertEqual(self.read("spinsLeft", "globals"), 2)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 2)
        self.worker.handle("resource", {"resource": "spins", "operation": "set", "value": 0})
        self.assertEqual(self.read("spinsLeft", "globals"), 0)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 0)

    def test_quick_supply_adds_gold_and_free_budget_together(self):
        self.worker.handle("quick_supply", {})
        self.assertEqual(self.read("coins", "globals"), 10100)
        self.assertEqual(self.read("spinsLeft", "globals"), 23)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 20)

    def test_quick_supply_preserves_existing_fractional_gold(self):
        self.lua.dostring("__trainer_globals.coins=100.5")
        self.worker.handle("quick_supply", {})
        self.assertEqual(self.read("coins", "globals"), 10100.5)
        self.assertEqual(self.read("spinsLeft", "globals"), 23)

    def test_quick_supply_failure_rolls_back_all_resources(self):
        budget = self.memory.resolve_path("spinsLeft", "globals")
        real_write = self.memory.write_number
        def fail_last(address, value):
            if address == budget and value == 23:
                raise RuntimeError("supply budget failed")
            return real_write(address, value)
        with mock.patch.object(self.memory, "write_number", side_effect=fail_last):
            with self.assertRaisesRegex(RuntimeError, "本次写入已恢复"):
                self.worker.handle("quick_supply", {})
        self.assertEqual(self.read("coins", "globals"), 100)
        self.assertEqual(self.read("spinsLeft", "globals"), 3)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 0)

    def test_quick_supply_rejects_menu_without_writes(self):
        self.lua.dostring("__trainer_globals.phase='menu'")
        with self.assertRaisesRegex(RuntimeError, "进入一局"):
            self.worker.handle("quick_supply", {})
        self.assertEqual(self.read("coins", "globals"), 100)

    def test_native_battle_speed_restores_boolean_and_nil(self):
        self.lua.dostring("__trainer_globals.battlefield.battleFastForwardLevel=nil")
        flag = self.memory.resolve_path("battlefield.battleFastForward", "globals")
        level = self.memory.resolve_path("battlefield.battleFastForwardLevel", "globals")
        self.session.sync(self.edits(["battle_speed"]))
        self.assertIs(self.memory.read_boolean(flag), True)
        self.assertEqual(self.memory.read_d(level), 2)
        self.session.sync({})
        self.assertIs(self.memory.read_boolean(flag), False)
        self.assertIsNone(self.memory.read_scalar(level))
        self.assertEqual(self.memory.read_q(level), 0xffffffffffffffff)

    def test_native_speed_partial_failure_restores_flag(self):
        real_write = self.memory.write_number
        def fail_target(address, value):
            if value == 2:
                raise RuntimeError("level failed")
            return real_write(address, value)
        with mock.patch.object(self.memory, "write_number", side_effect=fail_target):
            with self.assertRaisesRegex(RuntimeError, "本次写入已恢复"):
                self.session.sync(self.edits(["battle_speed"]))
        address = self.memory.resolve_path("battlefield.battleFastForward", "globals")
        self.assertIs(self.memory.read_boolean(address), False)

    def test_native_speed_before_first_use_has_no_level_key(self):
        self.lua.dostring("__trainer_globals.battlefield={wave={id=0,active=false},baseHP=7,battleFastForward=false}")
        self.worker.current_state()
        self.worker.enabled = {"battle_speed"}
        self.worker.apply()
        flag = self.memory.resolve_path("battlefield.battleFastForward", "globals")
        self.assertIs(self.memory.read_boolean(flag), True)
        self.worker.enabled.clear()
        self.worker.apply()
        self.assertIs(self.memory.read_boolean(flag), False)
        self.worker.enabled = {"battle_speed"}
        self.worker.values["battle_speed"] = 3
        with self.assertRaisesRegex(RuntimeError, "游戏中点一次加速按钮"):
            self.worker.apply()
        self.assertIs(self.memory.read_boolean(flag), False)

    def test_free_spin_batch_failure_restores_budget_and_allowance(self):
        real_write = self.memory.write_number
        budget = self.memory.resolve_path("spinsLeft", "globals")
        def fail_budget(address, value):
            if address == budget and value == 8:
                raise RuntimeError("budget failed")
            return real_write(address, value)
        with mock.patch.object(self.memory, "write_number", side_effect=fail_budget):
            with self.assertRaisesRegex(RuntimeError, "本次写入已恢复"):
                self.worker.handle("resource", {"resource": "spins", "operation": "add", "value": 5})
        self.assertEqual(self.read("spinsLeft", "globals"), 3)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 0)

    def test_menu_rejects_current_run_resource_writes(self):
        self.lua.dostring("__trainer_globals.phase='menu'")
        with self.assertRaisesRegex(RuntimeError, "进入一局"):
            self.worker.handle("resource", {"resource": "coins", "operation": "add", "value": 9})
        self.assertEqual(self.read("coins", "globals"), 100)

    def test_leaving_run_to_menu_stops_single_run_overrides(self):
        self.worker.keep_across_runs = False
        self.worker.enabled = {"reward"}
        self.worker.current_state()
        self.worker.apply()
        original = PARAMETERS["economy.wave.baseClear"][0]
        self.assertEqual(self.read("economy.wave.baseClear"), original * 2 + 400)
        self.lua.dostring("__trainer_globals.phase='menu'; __trainer_globals.battlefield=nil")
        self.worker.current_state()
        self.worker.apply()
        self.assertEqual(self.worker.enabled, set())
        self.assertEqual(self.read("economy.wave.baseClear"), original)

    def test_configuration_entry_and_restore(self):
        self.worker.handle("configure", {"enabled": ["paid_free", "free_wave"],
                                         "values": dict(self.values), "weights": dict(self.weights),
                                         "custom": {}, "keep_across_runs": True})
        self.assertEqual(self.read("spinCost", "globals"), 0)
        self.assertEqual(self.read("spinsLeft", "globals"), 10)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 10)
        self.worker.handle("restore", {})
        self.assertEqual(self.read("spinCost", "globals"), 100)
        self.assertEqual(self.read("spinsLeft", "globals"), 10)
        self.assertEqual(self.read("freeSpinCredits", "globals"), 10)

    def test_new_run_clears_scan_locks_and_can_stop_features(self):
        self.worker.keep_across_runs = False
        self.worker.current_state()
        self.worker.enabled = {"attack"}
        self.worker.apply()
        self.worker.scan_locks[123] = 44
        self.lua.dostring("__trainer_globals.battlefield={wave={id=0,active=false},baseHP=90}")
        self.worker.current_state()
        self.worker.apply()
        self.assertEqual(self.worker.enabled, set())
        self.assertEqual(self.worker.scan_locks, {})
        self.assertEqual(self.read("power.baseline.ATK0"), 9)

    def test_scanner_writes_selected_candidate_with_verification(self):
        first = self.memory.resolve_path("coins", "globals")
        second = self.memory.resolve_path("spinsLeft", "globals")
        self.worker.scanner.candidates = [first, second]
        self.worker.handle("scan_write", {"address": second, "value": 22, "lock": True})
        self.assertEqual(self.read("coins", "globals"), 100)
        self.assertEqual(self.read("spinsLeft", "globals"), 22)
        self.assertEqual(self.worker.scan_locks, {second: 22})

    def test_local_process_lifecycle(self):
        self.assertTrue(self.memory.alive())
        self.memory.close()
        self.assertFalse(self.memory.alive())

    def test_roots_are_discovered_from_live_gc64_heap(self):
        self.lua.dostring("package.loaded['src.data.constants']=__trainer_constants; love={}")
        with GameMemory(os.getpid()) as memory:
            self.assertEqual(memory.root_table(), self.memory._roots["constants"])
            self.assertIn("package", memory.table_nodes(memory.root_table("globals")))









class ValidationAndSaveTests(unittest.TestCase):
    def test_grade_percentages_reject_invalid_totals_and_values(self):
        for bad in [0, 99, 101, -1, "nan", "inf"]:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                grade_distribution(dict(DEFAULT_VALUES, grade_legendary=bad))
        probabilities = grade_distribution(dict(DEFAULT_VALUES, grade_legendary=.01, grade_normal=99.99))
        self.assertAlmostEqual(probabilities["legendary"], .0001)

    def test_grade_normalization_keeps_zero_entries_and_exact_total(self):
        values = dict(DEFAULT_VALUES, grade_normal=1, grade_rare=1, grade_legendary=1)
        normalized = normalize_grade_percentages(values)
        self.assertEqual(sum(normalized.values()), 100)
        self.assertEqual(normalized["advanced"], 0)
        self.assertEqual(normalized["prism"], 0)
        with self.assertRaisesRegex(ValueError, "大于 0"):
            normalize_grade_percentages({"grade_" + key: 0 for key in GRADES.values()})

    def test_hotkeys_register_on_game_foreground_and_report_conflicts(self):
        import hotkeys
        manager = hotkeys.Hotkeys()
        manager.game_pid = 4321
        native = mock.MagicMock()
        native.GetForegroundWindow.return_value = 1

        def foreground(window, process):
            process._obj.value = 4321
            return 1

        pending = iter((0, 2))
        def message(pointer, window, first, last, flag):
            identifier = next(pending, None)
            if identifier is not None:
                pointer._obj.wParam = identifier
                return 1
            return 0

        native.GetWindowThreadProcessId.side_effect = foreground
        native.RegisterHotKey.side_effect = lambda window, key, modifiers, code: key != 4
        native.PeekMessageW.side_effect = message
        with mock.patch.object(hotkeys.ctypes.windll, "user32", native):
            with mock.patch.object(manager.stop_requested, "wait", side_effect=[False, True]):
                manager.run()
        received = list(manager.events.queue)
        self.assertIn(("action", "once_win"), received)
        self.assertIn(("action", "quick_supply"), received)
        self.assertIn(("status", (True, False, (4,))), received)
        self.assertEqual(len(native.RegisterHotKey.call_args_list), 11)
        self.assertEqual(len(native.UnregisterHotKey.call_args_list), 10)

    def test_nonfinite_invalid_fractional_integer_and_out_of_range(self):
        for value in ["nan", "inf", "-inf", "", "x", -1, 1e20]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value, 0, 100)
        with self.assertRaises(ValueError):
            number(1.5, 0, 100, integer=True)
        self.assertEqual(number("0", 0, 10, integer=True), 0)

    @unittest.skipUnless(find_game_directory(), "Optional: provide SLOTBOUND_GAME_DIR for save integration")
    def test_save_edit_is_valid_and_backup_is_original(self):
        with tempfile.TemporaryDirectory(prefix="slotbound-save-test-") as directory:
            save = Path(directory) / "save.dat"
            backup = Path(directory) / "before.dat"
            original = "return {originite=7,achievements={runs=2,bosses=1,played=3}}"
            save.write_text(original, encoding="utf-8")
            with mock.patch.object(core, "SAVE", str(save)), mock.patch.object(core, "SAVE_BAK", str(backup)):
                core.edit_save(["s.originite=999", "s.achievements.runs=8"])
                self.assertEqual(core.read_save_field("s.originite"), "999")
                self.assertEqual(core.read_save_field("s.achievements.runs"), "8")
                self.assertEqual(backup.read_text(encoding="utf-8"), original)
                previous = save.read_bytes()
                with self.assertRaises(RuntimeError):
                    core.edit_save(["error('test failure before commit')"])
                self.assertEqual(save.read_bytes(), previous)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {"tests": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "passed": result.wasSuccessful(),
               "fixture": "user-provided lua51.dll and project-authored synthetic fixtures"}
    directory = Path("evidence/v" + VERSION)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "regression.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
