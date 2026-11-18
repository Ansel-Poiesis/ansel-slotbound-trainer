"""Windowless tests in both source and packaged builds. No game data is bundled."""
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest import mock

from trainer_engine import VERSION
import test_trainer
import test_ui_controller
import test_symbol_assets
import test_portability


def verify(output):
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module) for module in
                               (test_trainer, test_ui_controller, test_symbol_assets, test_portability))
    with mock.patch.object(tk.Tk, "__init__", side_effect=AssertionError("Windowless checks must not create Tk windows")):
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    target.with_suffix(".log").write_text(stream.getvalue(), encoding="utf-8")
    summary = {"version": VERSION, "utc": datetime.now(timezone.utc).isoformat(),
               "packaged": bool(getattr(sys, "frozen", False)), "passed": result.wasSuccessful(),
               "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": [{"test": str(case), "reason": why} for case, why in result.skipped],
               "windows_created": False, "game_process_launched": False, "active_saves_used": False,
               "scope": "Controller, portability, assets; optional synthetic LuaJIT GC64/save integration using user DLL",
               "original_game_algorithms_tested": False,
               "failed_tests": [{"test": str(case), "traceback": trace} for case, trace in result.failures + result.errors]}
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1
