"""Switch the clean Windows test installation to a different, user-selected path."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from setup_clean_install_test import APP_DIR, ROOT, SETUP, assert_registrations, command


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'Use a disposable Windows profile'
    original = Path(os.environ['LOCALAPPDATA']) / 'Veylo'
    assert_registrations(original)
    retained = original / 'Downloads/keep-old-download.txt'
    retained.write_text('preserve this file', encoding='utf-8')

    scratch = Path(tempfile.mkdtemp(prefix='veylo 安装验证 ', dir=os.environ['RUNNER_TEMP'])).resolve()
    occupied = scratch / 'occupied'
    occupied.mkdir()
    unrelated = occupied / 'other-data.txt'
    unrelated.write_text('unrelated user file', encoding='utf-8')
    rejected = subprocess.run([str(SETUP), '--quiet', '--install-dir', str(occupied)],
                              capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    assert rejected.returncode == 1 and '请选择空文件夹' in rejected.stderr.decode('utf-8')
    assert list(occupied.iterdir()) == [unrelated] and unrelated.read_text(encoding='utf-8') == 'unrelated user file'
    assert_registrations(original)

    install = scratch / '自选位置 Veylo'
    print(command(SETUP, '--quiet', '--install-dir', install, timeout=1000), flush=True)
    assert_registrations(install)
    app = install / APP_DIR
    assert (app / 'extension/manifest.json').is_file()
    assert (app / 'Veylo-Setup.exe').read_bytes() == SETUP.read_bytes()
    for kind, relative, vendor_dir in (
        ('fluent', 'native/fluent_paths.json', 'FluentYTDL-3.7.6'),
        ('bili', 'bilibili/native/bili_paths.json', 'Bili23-2.14.0'),
    ):
        config = json.loads((app / relative).read_text(encoding='utf-8'))
        assert Path(config['download_dir']) == install / 'Downloads'
        assert Path(config[kind + '_root']) == install / 'vendor' / vendor_dir
    assert retained.read_text(encoding='utf-8') == 'preserve this file'

    rerun = command(SETUP, '--quiet', timeout=60)
    assert ' MB' not in rerun, 'Reopening Setup must reuse the selected installation'
    assert_registrations(install)
    report_path = ROOT / 'dist/clean-install-report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report['customDirectory'] = {
        'replacedDefaultBrowserRegistrations': 'passed',
        'pathWithSpacesAndUnicode': 'passed',
        'differentDrive': original.drive.casefold() != install.drive.casefold(),
        'preservedOldDownloads': 'passed',
        'refusedOccupiedDirectory': 'passed',
        'rememberedDirectoryOnReinstall': 'passed',
    }
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as handle:
        handle.write(f'VEYLO_TEST_INSTALL_DIR={install}\n')
    print('PASS custom destination, browser registration switch, and remembered installation', flush=True)


if __name__ == '__main__':
    main()
