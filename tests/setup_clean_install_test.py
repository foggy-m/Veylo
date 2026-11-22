"""Run the deliverable on a fresh Windows VM and check its actual dependencies."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import winreg

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/'
HOSTS = {'com.veylo.youtube_x': 'native/ShiguangBridge.exe', 'com.veylo.bilibili': 'bilibili/native/BiliBridge.exe'}


def command(exe, *args, timeout=25):
    result = subprocess.run([str(exe), *map(str, args)], capture_output=True, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f'{exe.name} exited {result.returncode}: ' + result.stderr.decode('utf-8', 'replace')[-1500:])
    return result.stdout.decode('utf-8', 'replace')


def main():
    install = Path(os.environ['LOCALAPPDATA']) / 'Veylo'
    bili_profile = Path(os.environ['APPDATA']) / 'Bili23 Downloader' / 'config.json'
    assert not install.exists() and not bili_profile.exists(), 'Use an unused Windows profile'
    print('PASS clean Windows user has neither an installation nor a Bili23 profile', flush=True)
    exe = ROOT / 'dist/Veylo-2.4.6-Setup-x64.exe'
    assert command(exe, '--version').strip() == '2.4.6', 'GUI executable must support redirected output without a console'
    print(command(exe, '--quiet', timeout=1000), flush=True)
    app = install / 'app-2.4.5'
    fluent, bili = install / 'vendor/FluentYTDL-3.7.6', install / 'vendor/Bili23-2.14.0'
    assert json.loads((app / 'extension/manifest.json').read_text(encoding='utf-8'))['version'] == '2.4.5'
    for browser in ('Google/Chrome', 'Microsoft/Edge', 'Chromium'):
        for name, relative in HOSTS.items():
            key = fr'Software\{browser.replace("/", chr(92))}\NativeMessagingHosts\{name}'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                manifest, kind = winreg.QueryValueEx(handle, '')
            data = json.loads(Path(manifest).read_text(encoding='utf-8'))
            assert kind == winreg.REG_SZ and data['name'] == name
            assert Path(data['path']) == app / relative and data['allowed_origins'] == [ORIGIN]
    assert not bili_profile.exists() and not (fluent / 'config.json').exists(), 'Install must not require either vendor GUI'
    assert command(fluent / 'bin/yt-dlp/yt-dlp.exe', '--version').strip()
    deno = next(path for path in [fluent / 'bin/js/deno.exe', fluent / 'bin/deno/deno.exe'] if path.is_file())
    assert 'deno ' in command(deno, '--version')
    assert 'ffmpeg version' in command(fluent / 'bin/ffmpeg/ffmpeg.exe', '-version')
    assert 'ffmpeg version' in command(bili / 'bundle/ffmpeg.exe', '-version')
    check_bili = (
        'import pathlib,runpy,sys; '
        'module=runpy.run_path(sys.argv[1]); module["setup_bili"](pathlib.Path(sys.argv[2])); '
        'from util.common.config import config; from util.network.request import get_client; '
        'assert config.app_version == "2.14.0" and config.is_login.value is False; '
        'assert callable(get_client); print("Bili23 guest runtime ready")'
    )
    assert 'Bili23 guest runtime ready' in command(bili / 'runtime/python.exe', '-B', '-c', check_bili,
                                                   app / 'bilibili/driver/bd_driver.py', bili)
    assert not bili_profile.exists(), 'Bili23 runtime must load its guest defaults without a GUI'
    print('PASS downloaded tools, Deno, both FFmpeg builds, Bili23 guest Python imports, and HKCU registrations', flush=True)

    rerun = command(exe, '--quiet', timeout=40)
    assert not list((install / 'download-cache').glob('*.part'))
    assert ' MB' not in rerun, 'Rerunning Setup should not download dependencies again'
    report = {'setupSha256': hashlib.sha256(exe.read_bytes()).hexdigest(), 'freshWindowsInstallation': 'passed',
              'automaticDependencyDownloadAndExtraction': 'passed', 'bundledRuntimeAndEncoders': 'passed',
              'existingAccountOrVendorConfiguration': False, 'realCurrentUserBrowserRegistration': 'passed',
              'repeatInstallationReusedDependencies': 'passed', 'bilibiliGuestImports': 'passed'}
    (ROOT / 'dist/clean-install-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS clean installation and re-run', flush=True)


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true' and sys.argv[1:] != ['--clean-profile']:
        sys.exit('Run on a disposable Windows Actions runner or pass --clean-profile in a virgin Windows test profile.')
    main()
