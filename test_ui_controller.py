"""Controller contracts without creating a Tk interpreter or desktop window."""
from types import SimpleNamespace, MethodType
import unittest
from unittest import mock

from trainer_engine import DEFAULT_VALUES, DEFAULT_WEIGHTS, FEATURES, GRADES, GRADE_PRESETS, SYMBOLS, RuntimeWorker, compose_edits
from trainer_gui import NumberInput, Trainer


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Label:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class UIControllerTests(unittest.TestCase):
    def grade_owner(self):
        owner = SimpleNamespace(config_job=None, enabled={"grade"}, values=dict(DEFAULT_VALUES),
                                weights=dict(DEFAULT_WEIGHTS), custom={},
                                rows={feature.key: {"status": Label(), "toggle": Value(False)} for feature in FEATURES},
                                worker=RuntimeWorker(), keep_runs=Value(True), show_error=mock.Mock(),
                                grade_summary=Label(), grade_preset_var=Value("全部传说"),
                                grade_inputs={grade: Value(DEFAULT_VALUES["grade_" + grade]) for grade in GRADES.values()},
                                schedule_config=mock.Mock())
        owner.update_grade_summary = MethodType(Trainer.update_grade_summary, owner)
        owner.set_grade_percentages = MethodType(Trainer.set_grade_percentages, owner)
        owner.toggle_feature = MethodType(Trainer.toggle_feature, owner)
        return owner

    def test_icon_selection_commits_correct_internal_symbol(self):
        class Widget:
            def __init__(self, *args, **kwargs):
                self.options = kwargs
            def pack(self, *args, **kwargs): pass
            def grid(self, *args, **kwargs): pass
            def columnconfigure(self, *args, **kwargs): pass
            def winfo_children(self): return []
        owner = SimpleNamespace(values=dict(DEFAULT_VALUES), draw_options=Widget(), mode_hint=Label(),
                                symbol_images={key: object() for key in SYMBOLS}, schedule_config=mock.Mock())
        owner.set_value = MethodType(Trainer.set_value, owner)
        owner.select_symbol = MethodType(Trainer.select_symbol, owner)
        with mock.patch("trainer_gui.tk.Frame", Widget), mock.patch("trainer_gui.tk.Label", Widget), \
             mock.patch("trainer_gui.tk.Radiobutton", Widget), mock.patch("trainer_gui.tk.StringVar", Value):
            Trainer.mode_changed(owner, "同符号满盘", request=False)
        self.assertEqual(set(owner.symbol_buttons), set(SYMBOLS))
        for key, button in owner.symbol_buttons.items():
            self.assertIs(button.options["image"], owner.symbol_images[key])
            self.assertEqual(button.options["text"], SYMBOLS[key])
        owner.symbol_buttons["D"].options["command"]()
        self.assertEqual(owner.values["symbol"], "D")
        self.assertEqual(owner.symbol_var.get(), "D")
        edits = compose_edits({"draw"}, owner.values, DEFAULT_WEIGHTS, {}, lambda key: 1, True)
        self.assertEqual(edits[("constants", "slot.symbolWeights.D")], 1)
        self.assertEqual(edits[("constants", "slot.symbolWeights.W")], 0)

    def test_icon_weight_percentages_follow_current_weights(self):
        owner = SimpleNamespace(weights=dict(DEFAULT_WEIGHTS), weight_summary=Label(),
                                weight_share_labels={key: Label() for key in SYMBOLS})
        Trainer.update_weight_summary(owner)
        self.assertEqual(owner.weight_share_labels["D"].options["text"], "10%")
        owner.weights = {key: 1 if key == "M" else 0 for key in SYMBOLS}
        Trainer.update_weight_summary(owner)
        self.assertEqual(owner.weight_share_labels["M"].options["text"], "100%")
        self.assertEqual(owner.weight_share_labels["W"].options["text"], "0%")

    def test_quick_profiles_enable_quality_and_absolute_unit_multipliers(self):
        owner = self.grade_owner()
        owner.inputs = {key: Value(5) for key in ("attack", "health", "speed")}
        owner.enabled.clear()
        Trainer.quick_profile(owner, "rare")
        self.assertIn("grade", owner.enabled)
        self.assertEqual(owner.values["grade_rare"], 40)
        self.assertEqual(owner.values["grade_legendary"], 15)
        Trainer.quick_profile(owner, "legendary")
        self.assertEqual(owner.values["grade_legendary"], 100)
        Trainer.quick_profile(owner, "troops")
        Trainer.quick_profile(owner, "troops")
        self.assertTrue({"attack", "health", "speed"} <= owner.enabled)
        for key in ("attack", "health", "speed"):
            self.assertEqual(owner.values[key], 2)
            self.assertEqual(owner.inputs[key].get(), 2)

    def test_quick_resource_buttons_use_explicit_amounts(self):
        owner = SimpleNamespace(worker=RuntimeWorker())
        Trainer.quick_resource(owner, "coins", 10000)
        Trainer.quick_resource(owner, "spins", 20)
        self.assertEqual(owner.worker.commands.get_nowait(), ("resource", {"resource": "coins", "operation": "add", "value": 10000}))
        self.assertEqual(owner.worker.commands.get_nowait(), ("resource", {"resource": "spins", "operation": "add", "value": 20}))

    def test_grade_draft_waits_for_100_percent_before_queuing(self):
        owner = self.grade_owner()
        Trainer.grade_changed(owner, "legendary", 25)
        Trainer.configure_runtime(owner)
        self.assertTrue(owner.worker.commands.empty())
        self.assertEqual(owner.rows["grade"]["status"].options["text"], "待调整")
        self.assertIn("合计 25%", owner.grade_summary.options["text"])
        self.assertEqual(owner.grade_preset_var.get(), "自定义")
        Trainer.grade_changed(owner, "normal", 75)
        Trainer.configure_runtime(owner)
        action, data = owner.worker.commands.get_nowait()
        self.assertEqual(action, "configure")
        self.assertEqual(data["values"]["grade_legendary"], 25)
        self.assertEqual(data["values"]["grade_normal"], 75)

    def test_grade_invalid_entry_does_not_apply_a_previous_valid_value(self):
        owner = self.grade_owner()
        owner.grade_inputs["rare"].set("nan")
        Trainer.configure_runtime(owner)
        self.assertTrue(owner.worker.commands.empty())
        self.assertEqual(owner.rows["grade"]["status"].options["text"], "待调整")
        owner.show_error.assert_called_once()

    def test_grade_preset_updates_all_inputs_and_keeps_toggle_off(self):
        owner = self.grade_owner()
        owner.enabled.clear()
        Trainer.grade_preset_changed(owner, "稀有优先")
        self.assertEqual(owner.enabled, set())
        for key, value in GRADE_PRESETS["稀有优先"].items():
            self.assertEqual(owner.grade_inputs[key].get(), value)
            self.assertEqual(owner.values["grade_" + key], value)
        self.assertEqual(owner.grade_preset_var.get(), "稀有优先")
        owner.schedule_config.assert_called_once()

    def test_grade_normalize_uses_current_uncommitted_inputs(self):
        owner = self.grade_owner()
        owner.grade_inputs = {grade: Value(1 if grade in {"normal", "rare", "legendary"} else 0) for grade in GRADES.values()}
        Trainer.normalize_grades(owner)
        self.assertEqual(sum(control.get() for control in owner.grade_inputs.values()), 100)
        self.assertEqual(owner.values["grade_prism"], 0)
        self.assertIn("合计 100%", owner.grade_summary.options["text"])

    def test_grade_advanced_edit_directs_user_to_active_probability_panel(self):
        owner = self.grade_owner()
        owner.parameter_tree = mock.Mock()
        owner.parameter_tree.selection.return_value = ("unitGrades.probabilities.legendary",)
        owner.parameter_value = Value(.9)
        Trainer.parameter_apply(owner)
        self.assertEqual(owner.custom, {})
        owner.schedule_config.assert_not_called()
        self.assertIn("单位页", owner.show_error.call_args.args[0])

    def test_numeric_commit_step_zero_and_invalid_input(self):
        changed, errors = [], []
        control = SimpleNamespace(minimum=0, maximum=999, step=1, integer=True,
                                  var=Value("1"), entry=Label(), last_value=1,
                                  command=changed.append, error=errors.append)
        control.get = MethodType(NumberInput.get, control)
        control.commit = MethodType(NumberInput.commit, control)
        NumberInput.adjust(control, -1)
        self.assertEqual(changed, [0])
        control.var.set("nan")
        self.assertFalse(control.commit())
        self.assertEqual(control.last_value, 0)
        self.assertEqual(len(errors), 1)
        NumberInput.adjust(control, 1)
        self.assertEqual(changed, [0, 1])

    def test_configuration_uses_current_values_and_queues_worker_action(self):
        owner = SimpleNamespace(config_job=7, enabled={"free_wave", "attack", "refund"},
                                values=dict(DEFAULT_VALUES), weights=dict(DEFAULT_WEIGHTS), custom={},
                                rows={feature.key: {"status": Label()} for feature in FEATURES},
                                worker=RuntimeWorker(), keep_runs=Value(False), show_error=mock.Mock())
        owner.values.update(free_wave=0, attack=3, refund=200)
        Trainer.configure_runtime(owner)
        owner.show_error.assert_not_called()
        action, data = owner.worker.commands.get_nowait()
        self.assertEqual(action, "configure")
        self.assertEqual(data["values"]["free_wave"], 0)
        self.assertEqual(data["values"]["attack"], 3)
        self.assertEqual(data["values"]["refund"], 200)
        self.assertFalse(data["keep_across_runs"])

    def test_runtime_labels_distinguish_waiting_and_available_units(self):
        owner = SimpleNamespace(values=dict(DEFAULT_VALUES), rows={feature.key: {"status": Label()} for feature in FEATURES},
                                feedback=Label())
        data = {"enabled": ["paid_free", "attack", "free_wave"], "in_run": False,
                "unit_count": 0, "count": 0, "changed": 0}
        Trainer.handle_event(owner, "applied", data)
        self.assertEqual(owner.rows["paid_free"]["status"].options["text"], "待进入本局")
        data["in_run"] = True
        Trainer.handle_event(owner, "applied", data)
        self.assertEqual(owner.rows["attack"]["status"].options["text"], "等待招募单位")
        self.assertEqual(owner.rows["free_wave"]["status"].options["text"], "每波补充")
        data["unit_count"] = 1
        Trainer.handle_event(owner, "applied", data)
        self.assertEqual(owner.rows["attack"]["status"].options["text"], "已写入")

    def test_resource_display_does_not_add_free_allowance_to_total(self):
        owner = SimpleNamespace(stats={key: Label() for key in ("coins", "spins", "base_hp", "wave")})
        Trainer.handle_event(owner, "state", {"in_run": True, "coins": 500, "spins": 12,
                            "free_spins": 10, "base_hp": 7, "wave": 6})
        self.assertEqual(owner.stats["spins"].options["text"], "12 · 免费 10")
        self.assertEqual(owner.stats["wave"].options["text"], "6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
