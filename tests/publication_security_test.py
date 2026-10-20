"""Offline regressions for public-build filesystem containment."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("public_driver", Path(__file__).resolve().parents[1] / "bilibili/driver/bd_driver.py")
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class OutputBoundaryTests(unittest.TestCase):
    def test_ordinary_subdirectory_and_lexical_escape(self):
        with tempfile.TemporaryDirectory(prefix="veylo-path-check-") as temp:
            base = Path(temp)
            selected = base / "selected"
            selected.mkdir()
            self.assertEqual(DRIVER.checked_output_directory(selected / "new", selected), selected / "new")
            with self.assertRaises(RuntimeError):
                DRIVER.checked_output_directory(base / "outside", selected)
            with self.assertRaises(RuntimeError):
                DRIVER.checked_output_directory(selected / ".." / "outside", selected)

    def test_collection_title_cannot_follow_existing_link(self):
        with tempfile.TemporaryDirectory(prefix="veylo-link-check-") as temp:
            base = Path(temp).resolve()
            selected, outside = base / "selected", base / "outside"
            selected.mkdir()
            outside.mkdir()
            marker = outside / "preserve.txt"
            marker.write_text("unchanged", encoding="utf-8")
            link = selected / "remote-collection-title"
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                with self.assertRaises(RuntimeError):
                    DRIVER.checked_output_directory(link, selected)
                with self.assertRaises(RuntimeError):
                    DRIVER.checked_output_directory(link / "new-folder", selected)
                self.assertEqual(list(outside.iterdir()), [marker])
                self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            finally:
                # Remove only the exact link inside this verified temporary fixture.
                self.assertEqual(link.parent, base / "selected")
                if os.name == "nt":
                    os.rmdir(link)
                else:
                    link.unlink()


class FluentToolConfigurationTests(unittest.TestCase):
    def test_bili_audio_uses_explicit_shared_fluent_root(self):
        with tempfile.TemporaryDirectory(prefix="veylo-tool-config-") as temp:
            base = Path(temp)
            fluent = base / "full-tools"
            tool = fluent / "bin/ffmpeg/ffmpeg.exe"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"not executed; discovery fixture only")
            config = base / "fluent_paths.json"
            config.write_text(json.dumps({"fluent_root": str(fluent)}), encoding="utf-8")
            with patch.object(DRIVER, "FLUENT_PATHS", config, create=True), patch.object(Path, "home", return_value=base):
                self.assertEqual(DRIVER.audio_ffmpeg(base / "bili"), (str(tool), True))

    def test_configured_tool_root_rejects_existing_directory_link(self):
        with tempfile.TemporaryDirectory(prefix="veylo-tool-link-") as temp:
            base = Path(temp).resolve()
            actual = base / "actual-tools"
            tool = actual / "bin/ffmpeg/ffmpeg.exe"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"discovery-only placeholder")
            link = base / "linked-tools"
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(actual)], check=True, capture_output=True)
            else:
                link.symlink_to(actual, target_is_directory=True)
            try:
                config = base / "fluent_paths.json"
                config.write_text(json.dumps({"fluent_root": str(link)}), encoding="utf-8")
                with patch.object(DRIVER, "FLUENT_PATHS", config, create=True), patch.object(Path, "home", return_value=base):
                    self.assertEqual(DRIVER.audio_ffmpeg(base / "bili"), (str(base / "bili/bundle/ffmpeg.exe"), False))
                self.assertEqual(tool.read_bytes(), b"discovery-only placeholder")
            finally:
                self.assertEqual(link.parent, base)
                if os.name == "nt":
                    os.rmdir(link)
                else:
                    link.unlink()

    def test_invalid_fluent_configuration_does_not_execute_or_use_remote_tools(self):
        with tempfile.TemporaryDirectory(prefix="veylo-tool-reject-") as temp:
            base = Path(temp)
            config = base / "fluent_paths.json"
            expected = (str(base / "bili/bundle/ffmpeg.exe"), False)
            for value in ["relative/tools", "\\\\server\\share", "bad\u0000path", 123, None]:
                config.write_text(json.dumps({"fluent_root": value}), encoding="utf-8")
                with patch.object(DRIVER, "FLUENT_PATHS", config, create=True), patch.object(Path, "home", return_value=base):
                    self.assertEqual(DRIVER.audio_ffmpeg(base / "bili"), expected)


if __name__ == "__main__":
    unittest.main()
