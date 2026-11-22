"""Standard download messages through a release bridge with virgin tool folders."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/'
URL = 'https://www.youtube.com/watch?v=BaW_jenozKc'
GENERIC_ERROR = '本机桥接无法处理请求，请检查软件路径和文件权限。'


def call(executable, message, env=None):
    body = json.dumps(message).encode('utf-8')
    response = subprocess.run([str(executable), ORIGIN], input=struct.pack('<I', len(body)) + body,
                              capture_output=True, timeout=12, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    assert response.returncode == 0 and len(response.stdout) >= 4, (response.returncode, len(response.stdout))
    size = struct.unpack('<I', response.stdout[:4])[0]
    assert size == len(response.stdout) - 4
    return json.loads(response.stdout[4:])


def compile_bridge(target, source, isolated_bili=False):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    options = ['/define:BRIDGE_TEST'] if isolated_bili else []
    subprocess.run([str(compiler), '/nologo', '/target:exe', '/r:System.Windows.Forms.dll',
                    '/r:System.Web.Extensions.dll', '/out:' + str(target), *options, str(ROOT / source)],
                   check=True, timeout=30)


def await_fixture_worker(host, accepted, env=None):
    for _ in range(60):
        status = call(host, {'action': 'job-status', 'jobId': accepted['jobId']}, env)['job']['state']
        if status in ('failed', 'completed'):
            time.sleep(.2)
            return
        time.sleep(.1)
    raise AssertionError('Offline placeholder worker did not finish')


def cold_bili(home, downloads, blocked):
    tool, app = home / 'Bili23 portable', home / 'bilibili'
    host = app / 'native/BiliBridge.exe'
    host.parent.mkdir(parents=True)
    compile_bridge(host, 'bilibili/native/BiliBridge.cs', isolated_bili=True)
    for path in (tool / 'Bili23.exe', tool / 'runtime/python.exe', tool / 'bundle/ffmpeg.exe', app / 'driver/bd_driver.py'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'placeholder for cold start acceptance test')
    (host.parent / 'bili_paths.json').write_text(json.dumps({'bili_root': str(tool), 'download_dir': str(downloads)}), encoding='utf-8')
    absent = home / 'not-started-config.json'
    env = {**os.environ, 'BILI_TEST_CONFIG': str(absent)}
    request = {'action': 'bili-download', 'url': 'https://www.bilibili.com/video/BV1iW4113786/?p=4'}
    assert call(host, {'action': 'ping'}, env)['engineAvailable']
    accepted = call(host, request, env)
    assert accepted.get('ok') and accepted['login'] is False and Path(accepted['downloadDir']) == downloads, accepted
    await_fixture_worker(host, accepted, env)
    assert not absent.exists()
    absent.write_text('{', encoding='utf-8')
    invalid = call(host, request, env)
    assert not invalid['ok'] and 'Bili23 配置' in invalid['error'], invalid
    absent.write_text(json.dumps({'Download': {'download_path': str(downloads)}}), encoding='utf-8')
    denied = call(host, {**request, 'dir': str(blocked / 'download')}, env)
    assert not denied['ok'] and '保存目录' in denied['error'], denied


def main():
    with tempfile.TemporaryDirectory(prefix='veylo-cold-start-') as temp:
        home = Path(temp).resolve()
        tools, downloads, host = home / '工具 引擎', home / '下载 位置', home / 'ShiguangBridge.exe'
        compile_bridge(host, 'native/ShiguangBridge.cs')
        for name in ('bin/yt-dlp/yt-dlp.exe', 'bin/ffmpeg/ffmpeg.exe'):
            placeholder = tools / name
            placeholder.parent.mkdir(parents=True, exist_ok=True)
            # Never contacts a real platform. Windows refuses to start this placeholder.
            placeholder.write_bytes(b'placeholder for cold start acceptance test')
        (home / 'fluent_paths.json').write_text(json.dumps({'fluent_root': str(tools), 'download_dir': str(downloads)}),
                                               encoding='utf-8')
        assert call(host, {'action': 'ping'})['engineAvailable']
        request = {'action': 'yt-download', 'url': URL}
        accepted = call(host, request)
        assert accepted.get('error') != GENERIC_ERROR, 'Cold download reproduced the released generic bridge error'
        assert accepted.get('ok') and Path(accepted['downloadDir']) == downloads, accepted
        await_fixture_worker(host, accepted)
        assert not (tools / 'config.json').exists(), 'Bridge must work before opening FluentYTDL'
        (tools / 'config.json').write_text('{', encoding='utf-8')
        invalid = call(host, request)
        assert not invalid['ok'] and 'FluentYTDL 配置' in invalid['error'], invalid
        (tools / 'config.json').unlink()
        blocked = home / 'file-instead-of-directory'
        blocked.write_text('preserve', encoding='ascii')
        unavailable = call(host, {**request, 'dir': str(blocked / 'download')})
        assert not unavailable['ok'] and '保存目录' in unavailable['error'], unavailable
        assert blocked.read_text(encoding='ascii') == 'preserve'
        cold_bili(home, downloads, blocked)
    print('PASS FluentYTDL and Bili23 guest acceptance, malformed configuration and output folder guidance')


if __name__ == '__main__':
    sys.exit(main())
