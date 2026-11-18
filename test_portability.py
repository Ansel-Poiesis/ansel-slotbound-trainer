"""Publication regressions: no installation dependency; safe portable path/save handling."""
import json
import contextlib
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest import mock

import game_paths
import save_backend


class PortabilityTests(unittest.TestCase):
    def test_release_zip_has_root_executable_and_matching_provenance(self):
        from tools import package_release
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "dist").mkdir()
            executable = root / "dist/AnselSlotboundTrainer.exe"
            executable.write_bytes(b"MZ-synthetic-package-fixture")
            for name in ("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
                (root / name).write_text("package fixture", encoding="utf-8")
            (root / "THIRD_PARTY_LICENSES").mkdir()
            (root / "THIRD_PARTY_LICENSES/NOTICE.txt").write_text("fixture notice", encoding="utf-8")
            with mock.patch.object(package_release, "ROOT", root), \
                 mock.patch.object(package_release.subprocess, "check_output", side_effect=["a" * 40, ""]), \
                 contextlib.redirect_stdout(io.StringIO()):
                package_release.main()
            archive = next((root / "release").glob("*.zip"))
            with zipfile.ZipFile(archive) as bundle:
                self.assertIn("AnselSlotboundTrainer.exe", bundle.namelist())
                self.assertTrue(all("\\" not in name for name in bundle.namelist()))
                self.assertIn("THIRD_PARTY_LICENSES/NOTICE.txt", bundle.namelist())
                provenance = json.loads(bundle.read("provenance.json"))
                self.assertEqual(provenance["source_commit"], "a" * 40)
                self.assertEqual(provenance["executable_sha256"], hashlib.sha256(bundle.read("AnselSlotboundTrainer.exe")).hexdigest())

    def test_directory_picker_recovers_from_stale_installation_path(self):
        from trainer_gui import Trainer
        owner = SimpleNamespace(log_message=mock.Mock(), show_error=mock.Mock())
        with mock.patch("trainer_gui.find_game_directory", side_effect=ValueError("stale path")), \
             mock.patch("trainer_gui.filedialog.askdirectory", return_value="new folder"), \
             mock.patch("trainer_gui.save_directory", return_value=Path("new folder")) as save:
            Trainer.choose_game_directory(owner)
        save.assert_called_once_with("new folder")
        owner.show_error.assert_not_called()
        self.assertEqual(owner.log_message.call_count, 2)

    def test_cancelled_directory_picker_does_not_write_configuration(self):
        from trainer_gui import Trainer
        owner = SimpleNamespace(log_message=mock.Mock(), show_error=mock.Mock())
        with mock.patch("trainer_gui.find_game_directory", return_value=None), \
             mock.patch("trainer_gui.filedialog.askdirectory", return_value=""), \
             mock.patch("trainer_gui.save_directory") as save:
            Trainer.choose_game_directory(owner)
        save.assert_not_called()

    def test_engine_import_does_not_load_archive_or_decompiler(self):
        import trainer_engine
        self.assertEqual(trainer_engine.VERSION, "4.4.1")
        self.assertNotIn("sbmod_core", sys.modules)
        self.assertNotIn("pefile", sys.modules)
        self.assertNotIn("cryptography", sys.modules)
        self.assertNotIn("ljd", sys.modules)

    def test_no_game_installation_returns_none(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"SLOTBOUND_GAME_DIR": ""}), \
             mock.patch.object(game_paths, "config_file", return_value=Path(temp) / "none.json"), \
             mock.patch.object(game_paths, "running_directory", return_value=None), \
             mock.patch.object(game_paths, "steam_roots", return_value=[]):
            self.assertIsNone(game_paths.find_game_directory())

    def test_explicit_invalid_directory_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"SLOTBOUND_GAME_DIR": temp}):
            with self.assertRaisesRegex(ValueError, "lua51.dll"):
                game_paths.find_game_directory()

    def test_chinese_space_directory_persists_and_loads(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"SLOTBOUND_GAME_DIR": ""}):
            game = Path(temp) / "游戏 目录"
            game.mkdir()
            for file in ("Slotbound.exe", "lua51.dll"):
                (game / file).touch()
            settings = Path(temp) / "profile/settings.json"
            with mock.patch.object(game_paths, "config_file", return_value=settings):
                game_paths.save_directory(game)
                self.assertEqual(game_paths.find_game_directory(), game.resolve())
                self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["game_directory"], str(game.resolve()))

    def test_steam_manifest_supports_custom_install_folder(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"SLOTBOUND_GAME_DIR": ""}):
            library = Path(temp)
            game = library / "steamapps/common/Custom Slotbound"
            game.mkdir(parents=True)
            for file in ("Slotbound.exe", "lua51.dll"):
                (game / file).touch()
            (library / "steamapps/appmanifest_4459590.acf").write_text('"installdir" "Custom Slotbound"', encoding="utf-8")
            with mock.patch.object(game_paths, "config_file", return_value=library / "none.json"), \
                 mock.patch.object(game_paths, "running_directory", return_value=None), \
                 mock.patch.object(game_paths, "steam_roots", return_value=[library]):
                self.assertEqual(game_paths.find_game_directory(), game.resolve())

    def test_invalid_selection_does_not_replace_saved_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "settings.json"
            target.write_text('{"previous": true}', encoding="utf-8")
            with mock.patch.object(game_paths, "config_file", return_value=target), self.assertRaises(ValueError):
                game_paths.save_directory(temp)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"previous": true}')

    def test_save_concurrent_change_is_preserved_and_temp_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "save.dat"
            backup = Path(temp) / "save.bak"
            path.write_text("return {x=1}", encoding="utf-8")
            class ConcurrentLua:
                def dostring(self, code):
                    path.write_text("return {x=2}", encoding="utf-8")
                    return "return {x=999}"
            with mock.patch.object(save_backend, "SAVE", str(path)), mock.patch.object(save_backend, "SAVE_BAK", str(backup)):
                with self.assertRaisesRegex(RuntimeError, "发生变化"):
                    save_backend.edit_save(["s.x=999"], lua_state=ConcurrentLua())
            self.assertEqual(path.read_text(encoding="utf-8"), "return {x=2}")
            self.assertFalse(list(Path(temp).glob(".sbmod-*")))
