"""Pure mock installer tests: no native process, registry, desktop or browser access.

Run with: python -B tests/installer_test.py
"""
import base64
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


REGISTRY = types.ModuleType('winreg')
REGISTRY.HKEY_CURRENT_USER = object()
REGISTRY.REG_SZ = 1
REGISTRY.OpenKey = MagicMock()
REGISTRY.QueryValueEx = MagicMock()
REGISTRY.CreateKey = MagicMock()
REGISTRY.SetValueEx = MagicMock()
SPEC = importlib.util.spec_from_file_location(
    'installer_under_test', Path(__file__).resolve().parents[1] / 'install_extension.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, winreg=REGISTRY):
    SPEC.loader.exec_module(INSTALLER)


def frame(value):
    body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return struct.pack('<I', len(body)) + body


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.eid = 'a' * 32
        self.response = {'ok': True, 'version': INSTALLER.HOST_VERSION, 'engineAvailable': True}
        self.manifest = {
            'name': INSTALLER.HOST_NAME, 'description': 'old description',
            'path': str(INSTALLER.HOST_EXE), 'type': 'stdio',
            'allowed_origins': [f'chrome-extension://{self.eid}/'],
        }
        for name in ('OpenKey', 'QueryValueEx', 'CreateKey', 'SetValueEx'):
            getattr(REGISTRY, name).reset_mock(return_value=True, side_effect=True)
        REGISTRY.OpenKey.side_effect = FileNotFoundError
        self.start(patch.object(Path, 'is_file', return_value=True))
        self.start(patch.object(Path, 'is_symlink', return_value=False))
        self.exists = self.start(patch.object(Path, 'exists', return_value=False))
        self.read = self.start(patch.object(Path, 'read_text', side_effect=AssertionError('unexpected file read')))
        self.start(patch.object(INSTALLER, 'extension_id', return_value=self.eid))
        self.run = self.start(patch.object(INSTALLER.subprocess, 'run', return_value=types.SimpleNamespace(
            stdout=frame(self.response), stderr=b'', returncode=0)))
        self.start(patch.object(INSTALLER.subprocess, 'CREATE_NO_WINDOW', 0, create=True))
        self.replace = self.start(patch.object(INSTALLER.os, 'replace', side_effect=AssertionError('unexpected replace')))
        self.rename = self.start(patch.object(INSTALLER.os, 'rename', side_effect=AssertionError('unexpected rename')))
        self.unlink = self.start(patch.object(Path, 'unlink'))
        self.tempfile = self.start(patch.object(INSTALLER.tempfile, 'NamedTemporaryFile', side_effect=AssertionError('unexpected temp file')))
        self.tempdir = self.start(patch.object(INSTALLER.tempfile, 'TemporaryDirectory', side_effect=AssertionError('unexpected temp directory')))
        self.output = self.start(patch('builtins.print'))

    def start(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def existing_manifest(self, value=None):
        self.exists.return_value = True
        self.read.side_effect = None
        self.read.return_value = json.dumps(self.manifest if value is None else value)

    def registry_values(self, values, events=None):
        def open_key(_hive, key):
            if events is not None:
                events.append(('read', key))
            if key not in values:
                raise FileNotFoundError(key)
            handle = MagicMock()
            handle.key = key
            handle.__enter__.return_value = handle
            return handle

        def query(handle, _name):
            value = values[handle.key]
            if isinstance(value, Exception):
                raise value
            return value

        REGISTRY.OpenKey.side_effect = open_key
        REGISTRY.QueryValueEx.side_effect = query

    def keys(self):
        return [root + '\\' + INSTALLER.HOST_NAME for root in INSTALLER.REGISTRY_ROOTS]

    def test_registration_preflights_every_key_before_first_write(self):
        events = []
        self.registry_values({self.keys()[1]: (str(INSTALLER.HOST_MANIFEST), REGISTRY.REG_SZ)}, events)
        with patch.object(INSTALLER, 'write_manifest', side_effect=lambda value: events.append(('manifest', value))) as writer:
            REGISTRY.CreateKey.side_effect = lambda *_: events.append(('create', None)) or MagicMock()
            INSTALLER.register_host()
        self.assertEqual(events[:len(self.keys())], [('read', key) for key in self.keys()])
        self.assertEqual(events[len(self.keys())][0], 'manifest')
        self.assertEqual(REGISTRY.SetValueEx.call_count, len(self.keys()))
        self.assertEqual(writer.call_args.args[0]['allowed_origins'], self.manifest['allowed_origins'])
        self.assertEqual(self.run.call_count, 1)

    def test_late_registry_conflict_never_writes_manifest_or_any_key(self):
        self.existing_manifest()
        self.registry_values({self.keys()[-1]: (str(INSTALLER.ROOT / 'other.json'), REGISTRY.REG_SZ)})
        with patch.object(INSTALLER, 'write_manifest') as writer:
            with self.assertRaises(RuntimeError):
                INSTALLER.register_host()
        self.assertEqual(REGISTRY.OpenKey.call_count, len(self.keys()))
        writer.assert_not_called()
        REGISTRY.CreateKey.assert_not_called()
        REGISTRY.SetValueEx.assert_not_called()
        self.run.assert_not_called()

    def test_existing_unknown_registry_values_are_not_treated_as_absent(self):
        cases = [FileNotFoundError('missing default'), (str(INSTALLER.HOST_MANIFEST), 2),
                 (123, REGISTRY.REG_SZ), ('relative.json', REGISTRY.REG_SZ)]
        for value in cases:
            with self.subTest(value=value), patch.object(INSTALLER, 'write_manifest') as writer:
                self.registry_values({self.keys()[-1]: value})
                with self.assertRaises(RuntimeError):
                    INSTALLER.register_host()
                writer.assert_not_called()
        REGISTRY.CreateKey.assert_not_called()

    def test_manifest_ownership_fields_are_all_checked_before_writes(self):
        cases = [[], {'name': 'other'}, {'type': 'pipe'}, {'path': str(INSTALLER.ROOT / 'other.exe')},
                 {'path': 5}, {'allowed_origins': []}, {'allowed_origins': self.manifest['allowed_origins'] + ['chrome-extension://' + 'b' * 32 + '/']}]
        for change in cases:
            old = dict(self.manifest, **change) if isinstance(change, dict) else change
            with self.subTest(old=old), patch.object(INSTALLER, 'write_manifest') as writer:
                self.existing_manifest(old)
                with self.assertRaises(RuntimeError):
                    INSTALLER.register_host()
                writer.assert_not_called()
        REGISTRY.CreateKey.assert_not_called()

    def test_malformed_manifest_and_registry_access_errors_stop_before_writes(self):
        self.exists.return_value = True
        self.read.side_effect = None
        self.read.return_value = '{'
        with patch.object(INSTALLER, 'write_manifest') as writer:
            with self.assertRaises(RuntimeError):
                INSTALLER.register_host()
            self.exists.return_value = False
            REGISTRY.OpenKey.side_effect = PermissionError('denied')
            with self.assertRaises(PermissionError):
                INSTALLER.register_host()
        writer.assert_not_called()
        REGISTRY.CreateKey.assert_not_called()

    def test_matching_existing_manifest_is_accepted(self):
        self.existing_manifest()
        with patch.object(INSTALLER, 'write_manifest') as writer:
            INSTALLER.register_host()
        writer.assert_called_once()
        self.assertEqual(REGISTRY.SetValueEx.call_count, len(self.keys()))

    def test_valid_ping_uses_native_frame_and_returns_response(self):
        self.assertEqual(INSTALLER.ping(), self.response)
        self.assertEqual(self.run.call_args.kwargs['input'], frame({'action': 'ping'}))
        self.assertEqual(self.run.call_args.kwargs['timeout'], 15)

    def test_malformed_ping_frames_and_json_are_rejected(self):
        valid = frame(self.response)
        bad = [b'', b'\x02\x00', struct.pack('<I', 0), struct.pack('<I', 65537),
               valid[:-1], valid + b'X', valid + valid, struct.pack('<I', 1) + b'{',
               struct.pack('<I', 2) + b'\xff\xfe', frame([]), frame(None),
               struct.pack('<I', 4) + '{}'.encode('utf-16-le')]
        for wire in bad:
            with self.subTest(wire=wire):
                self.run.return_value.stdout = wire
                with self.assertRaises(RuntimeError):
                    INSTALLER.ping()

    def test_ping_requires_true_ok_expected_version_available_engine_and_exit_zero(self):
        bad = [{'ok': value} for value in (False, 1, 'true', None)]
        bad += [{'version': value} for value in ('1.0.0', '', None, 1)]
        bad += [{'engineAvailable': value} for value in (False, 1, 'true', None)]
        for change in bad:
            with self.subTest(change=change):
                self.run.return_value.stdout = frame(dict(self.response, **change))
                with self.assertRaises(RuntimeError):
                    INSTALLER.ping()
        self.run.return_value.stdout = frame(self.response)
        self.run.return_value.returncode = 1
        with patch.object(INSTALLER, 'write_manifest') as writer:
            with self.assertRaises(RuntimeError):
                INSTALLER.register_host()
        writer.assert_not_called()
        REGISTRY.CreateKey.assert_not_called()

    def atomic_fixture(self):
        handle = MagicMock()
        handle.name = str(INSTALLER.HOST_MANIFEST) + '.unique.tmp'
        handle.__enter__.return_value = handle
        self.tempfile.side_effect = None
        self.tempfile.return_value = handle
        self.replace.side_effect = None
        return handle

    def test_manifest_atomic_write_closes_temp_before_replace_and_cleans_up(self):
        handle = self.atomic_fixture()
        def replace(source, destination):
            handle.__exit__.assert_called_once()
            self.assertEqual(source, Path(handle.name))
            self.assertEqual(destination, INSTALLER.HOST_MANIFEST)
        self.replace.side_effect = replace
        INSTALLER.write_manifest(self.manifest)
        self.assertEqual(json.loads(handle.write.call_args.args[0]), self.manifest)
        self.assertEqual(self.tempfile.call_args.kwargs['dir'], INSTALLER.HOST_MANIFEST.parent)
        self.assertTrue(self.tempfile.call_args.kwargs['delete'] is False)
        self.replace.assert_called_once()
        self.unlink.assert_called_once_with(missing_ok=True)

    def test_failed_temp_write_or_replace_preserves_destination_and_cleans_temp(self):
        for stage in ('write', 'replace'):
            with self.subTest(stage=stage):
                handle = self.atomic_fixture()
                self.replace.reset_mock()
                self.unlink.reset_mock()
                if stage == 'write':
                    handle.write.side_effect = OSError('disk full')
                else:
                    self.replace.side_effect = PermissionError('destination locked')
                with self.assertRaises(OSError):
                    INSTALLER.write_manifest(self.manifest)
                if stage == 'write':
                    self.replace.assert_not_called()
                self.unlink.assert_called_once_with(missing_ok=True)

    def shortcut_metadata(self):
        exe = INSTALLER.ROOT / 'mock-browser' / 'browser.exe'
        url = 'edge://extensions/'
        metadata = {'TargetPath': str(exe), 'Arguments': url, 'WorkingDirectory': str(exe.parent),
                    'Description': '打开扩展管理页，加载 ' + str(INSTALLER.EXT_DIR)}
        return exe, url, metadata

    def test_known_shortcut_is_reused_without_save(self):
        exe, url, metadata = self.shortcut_metadata()
        self.exists.return_value = True
        self.run.return_value.stdout = json.dumps(metadata).encode('utf-8')
        self.assertTrue(INSTALLER.make_shortcut('Edge', exe, url))
        script = base64.b64decode(self.run.call_args.args[0][-1]).decode('utf-16-le')
        self.assertNotIn('.Save()', script)
        self.rename.assert_not_called()
        self.tempdir.assert_not_called()

    def test_unknown_or_unreadable_shortcut_is_preserved_and_skipped(self):
        exe, url, metadata = self.shortcut_metadata()
        self.exists.return_value = True
        cases = [b'not json', b'[]']
        for field in metadata:
            changed = dict(metadata)
            changed[field] = 'unknown'
            cases.append(json.dumps(changed).encode('utf-8'))
        for wire in cases:
            with self.subTest(wire=wire):
                self.run.return_value.stdout = wire
                self.assertFalse(INSTALLER.make_shortcut('Edge', exe, url))
        self.run.side_effect = subprocess.CalledProcessError(1, 'mock powershell')
        self.assertFalse(INSTALLER.make_shortcut('Edge', exe, url))
        self.rename.assert_not_called()
        self.unlink.assert_not_called()
        self.tempdir.assert_not_called()

    def test_new_shortcut_is_staged_and_never_overwrites_concurrent_destination(self):
        exe, url, _metadata = self.shortcut_metadata()
        directory = INSTALLER.DESKTOP / 'mock-shortcut-temp'
        self.tempdir.side_effect = None
        self.tempdir.return_value.__enter__.return_value = str(directory)
        self.rename.side_effect = None
        self.assertTrue(INSTALLER.make_shortcut('Edge', exe, url))
        path = INSTALLER.DESKTOP / 'Edge（安装 Veylo）.lnk'
        self.rename.assert_called_once_with(directory / path.name, path)
        script = base64.b64decode(self.run.call_args.args[0][-1]).decode('utf-16-le')
        self.assertIn(INSTALLER.ps_quote(directory / path.name), script)
        self.assertIn('.Save()', script)
        self.rename.side_effect = FileExistsError('created by user')
        self.assertFalse(INSTALLER.make_shortcut('Edge', exe, url))
        self.unlink.assert_not_called()
        self.replace.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
