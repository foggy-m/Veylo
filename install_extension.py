# -*- coding: utf-8 -*-
"""Register the local download bridge and generate browser installation shortcuts."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import winreg

ROOT = Path(__file__).resolve().parent
EXT_DIR = ROOT / 'extension'
HOST_NAME = 'com.shiguang.fluentytdl_bridge'
HOST_MANIFEST = ROOT / 'native' / (HOST_NAME + '.json')
HOST_EXE = ROOT / 'native' / 'ShiguangBridge.exe'
HOST_VERSION = '1.7.0'
MAX_PING_FRAME_SIZE = 65536
DESKTOP = Path.home() / 'Desktop'
BROWSERS = [
    ('Tabbit', 'chrome://extensions/', [Path(os.environ.get('LOCALAPPDATA', '')) / 'Tabbit Browser/Application/Tabbit Browser.exe']),
    ('Edge', 'edge://extensions/', [Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'), Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe')]),
    ('Chrome', 'chrome://extensions/', [Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'), Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'), Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe']),
]
REGISTRY_ROOTS = [
    r'Software\Google\Chrome\NativeMessagingHosts',
    r'Software\Microsoft\Edge\NativeMessagingHosts',
    r'Software\Chromium\NativeMessagingHosts',
    r'Software\Tabbit Browser\Tabbit Browser\NativeMessagingHosts',
    r'Software\Tabbit\NativeMessagingHosts',
]


def extension_id():
    manifest = json.loads((EXT_DIR / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'], validate=True)).hexdigest()[:32]
    return ''.join(chr(97 + int(c, 16)) for c in digest)


def ping():
    import struct
    payload = b'{"action":"ping"}'
    result = subprocess.run([str(HOST_EXE)], input=struct.pack('<I', len(payload)) + payload,
                            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError('本机桥接自检退出异常')
    if len(result.stdout) < 4:
        raise RuntimeError('本机桥接未返回有效消息')
    size = struct.unpack('<I', result.stdout[:4])[0]
    if not 0 < size <= MAX_PING_FRAME_SIZE or len(result.stdout) != 4 + size:
        raise RuntimeError('本机桥接返回的消息帧长度无效')
    try:
        response = json.loads(result.stdout[4:].decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('本机桥接返回的消息不是有效 UTF-8 JSON') from exc
    if not isinstance(response, dict) or response.get('ok') is not True:
        raise RuntimeError('本机桥接自检失败')
    if response.get('version') != HOST_VERSION:
        raise RuntimeError('本机桥接版本与安装器不符')
    if response.get('engineAvailable') is not True:
        raise RuntimeError('本机桥接下载引擎不可用或状态无效')
    return response


def same_path(value, expected):
    return (isinstance(value, str) and Path(value).is_absolute()
            and Path(value) == expected)


def preflight_host(manifest):
    if HOST_MANIFEST.is_symlink():
        raise RuntimeError('现有主机清单是符号链接，未覆盖')
    if HOST_MANIFEST.exists():
        try:
            old = json.loads(HOST_MANIFEST.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError('无法校验现有主机清单，未覆盖') from exc
        if (not isinstance(old, dict) or old.get('name') != HOST_NAME
                or old.get('type') != 'stdio' or not same_path(old.get('path'), HOST_EXE)
                or old.get('allowed_origins') != manifest['allowed_origins']):
            raise RuntimeError('现有主机清单与预期不符，未覆盖')
    keys = [root + '\\' + HOST_NAME for root in REGISTRY_ROOTS]
    for key in keys:
        try:
            existing = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            continue
        with existing:
            try:
                old_path, value_type = winreg.QueryValueEx(existing, '')
            except FileNotFoundError as exc:
                raise RuntimeError('已有桥接注册缺少默认值，未覆盖：' + key) from exc
        if value_type != winreg.REG_SZ or not same_path(old_path, HOST_MANIFEST):
            raise RuntimeError('已有其他或未知桥接注册，未覆盖：' + key)
    return keys


def write_manifest(manifest):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=HOST_MANIFEST.parent,
                                         prefix=HOST_MANIFEST.name + '.', suffix='.tmp',
                                         delete=False) as handle:
            temp = Path(handle.name)
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        os.replace(temp, HOST_MANIFEST)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def register_host():
    if not HOST_EXE.is_file():
        raise RuntimeError('找不到 native\\ShiguangBridge.exe，请先运行 native\\build.bat')
    eid = extension_id()
    manifest = {'name': HOST_NAME, 'description': 'Veylo local YouTube / X download bridge',
                'path': str(HOST_EXE), 'type': 'stdio', 'allowed_origins': [f'chrome-extension://{eid}/']}
    keys = preflight_host(manifest)
    ping()
    write_manifest(manifest)
    for key in keys:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, '', 0, winreg.REG_SZ, str(HOST_MANIFEST))
    print('本机下载桥接已注册，自检通过。扩展 ID：' + eid)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_shortcut_script(script):
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return subprocess.run(['powershell', '-NoProfile', '-EncodedCommand', encoded], check=True,
                          capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)


def make_shortcut(name, exe, url):
    path = DESKTOP / (name + '（安装 Veylo）.lnk')
    description = '打开扩展管理页，加载 ' + str(EXT_DIR)
    if path.is_symlink():
        print(name + '：现有快捷方式是符号链接，已保留并跳过。')
        return False
    if path.exists():
        ps = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        ps += '$s=(New-Object -ComObject WScript.Shell).CreateShortcut(' + ps_quote(path) + '); '
        ps += '@{TargetPath=$s.TargetPath; Arguments=$s.Arguments; WorkingDirectory=$s.WorkingDirectory; Description=$s.Description} | ConvertTo-Json -Compress'
        try:
            old = json.loads(run_shortcut_script(ps).stdout.decode('utf-8-sig'))
            known = (isinstance(old, dict) and same_path(old.get('TargetPath'), exe)
                     and old.get('Arguments') == url and same_path(old.get('WorkingDirectory'), exe.parent)
                     and old.get('Description') == description)
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
            known = False
        if not known:
            print(name + '：无法确认现有快捷方式归属，已保留并跳过。')
            return False
        return True
    with tempfile.TemporaryDirectory(prefix='shiguang-shortcut-', dir=DESKTOP) as temp_dir:
        temp = Path(temp_dir) / path.name
        ps = "$ErrorActionPreference='Stop'; $s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + ps_quote(temp) + '); '
        ps += '$s.TargetPath=' + ps_quote(exe) + '; $s.Arguments=' + ps_quote(url) + '; '
        ps += '$s.WorkingDirectory=' + ps_quote(exe.parent) + '; $s.Description=' + ps_quote(description) + '; $s.Save()'
        run_shortcut_script(ps)
        try:
            # Windows rename fails if the destination appeared since the check above.
            os.rename(temp, path)
        except FileExistsError:
            print(name + '：快捷方式已被其他程序创建，已保留并跳过。')
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register-only', action='store_true')
    args = parser.parse_args()
    try:
        register_host()
        if not args.register_only:
            for name, url, candidates in BROWSERS:
                exe = next((p for p in candidates if p.is_file()), None)
                if exe:
                    if make_shortcut(name, exe, url):
                        print(name + '：安装入口快捷方式已就绪。')
                else:
                    print(name + '：本机未检测到，安装浏览器后可使用同一份扩展。')
        print('浏览器扩展管理页 → 开发者模式 → 加载未打包的扩展程序 → ' + str(EXT_DIR))
        extension = json.loads((EXT_DIR / 'manifest.json').read_text(encoding='utf-8'))
        print('快捷方式只打开安装页；浏览器显示“' + extension['name'] + ' ' + extension['version'] + '”才表示扩展已加载。')
        return 0
    except Exception as exc:
        print('安装失败：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
