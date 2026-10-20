"""Offline Native Messaging and worker tests; real profiles and media are never opened."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
ORIGIN = 'chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/'


def compile_cs(source, output, define=None):
    args = [str(CSC), '/nologo', '/target:exe', '/out:' + str(output)]
    if define:
        args.append('/define:' + define)
    subprocess.run(args + [str(source)], check=True, capture_output=True)


def frame(message):
    body = json.dumps(message, ensure_ascii=False).encode('utf-8')
    return struct.pack('<I', len(body)) + body


def decode(data):
    result = []
    while data:
        assert len(data) >= 4, 'short response header'
        size = struct.unpack('<I', data[:4])[0]
        assert 0 < size <= 65536 and len(data) >= size + 4, 'invalid response frame'
        result.append(json.loads(data[4:4 + size]))
        data = data[4 + size:]
    return result


def exchange(exe, messages, env=None, origin=None):
    result = subprocess.run([str(exe)] + ([origin] if origin else []), input=b''.join(map(frame, messages)),
                            capture_output=True, timeout=15, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, 'host unexpectedly exited'
    assert not result.stderr, 'host printed unstructured error'
    responses = decode(result.stdout)
    assert len(responses) == len(messages), 'missing response'
    return responses


def request(exe, message, env):
    return exchange(exe, [message], env)[0]


class FixtureDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            jobs = Path(self.name) / 'native/jobs'
            active = False
            if jobs.is_dir():
                for file in jobs.glob('*.json'):
                    try:
                        active |= json.loads(file.read_text(encoding='utf-8')).get('state') in ('queued', 'downloading')
                    except (OSError, ValueError):
                        active = True
            if not active:
                break
            time.sleep(.1)
        for attempt in range(20):
            try:
                super().cleanup()
                return
            except PermissionError:
                time.sleep(.1)
        super().cleanup()


def main():
    production = ROOT / 'native/ShiguangBridge.exe'
    checks = 0
    response = exchange(production, [{'action': 'ping'}, {'action': 'not-supported'},
        {'action': 'job-status', 'jobId': '../bad'}, {'action': 'x-download', 'url': 'https://example.invalid/中文'}])
    assert response[0] == {'ok': True, 'version': '1.1.0', 'engineAvailable': True}
    assert all(r['ok'] is False for r in response[1:])
    checks += 4
    assert exchange(production, [{'action': 'ping'}], origin=ORIGIN)[0]['ok']
    bad_origin = subprocess.run([str(production), 'chrome-extension://' + 'b' * 32 + '/'], input=frame({'action': 'ping'}), capture_output=True, timeout=15)
    assert bad_origin.returncode != 0 and not bad_origin.stdout
    checks += 2
    for wire in [b'\x04\x00', struct.pack('<I', 0), struct.pack('<I', 65537), struct.pack('<I', 10) + b'{}']:
        malformed = subprocess.run([str(production)], input=wire, capture_output=True, timeout=15)
        assert malformed.returncode != 0 and not malformed.stdout
        checks += 1
    invalid_json = subprocess.run([str(production)], input=struct.pack('<I', 2) + b'\xff\xfe', capture_output=True, timeout=15)
    assert decode(invalid_json.stdout)[0]['ok'] is False
    checks += 1

    with FixtureDirectory(prefix='bridge-', dir=ROOT / 'tests') as temp:
        fixture = Path(temp)
        app = fixture / 'FluentYTDL'
        host_dir = fixture / 'native'
        host_dir.mkdir()
        (app / 'bin/yt-dlp').mkdir(parents=True)
        (app / 'bin/ffmpeg').mkdir()
        downloads = fixture / '模拟下载 folder'
        exe = host_dir / 'ShiguangBridge.exe'
        compile_cs(ROOT / 'native/ShiguangBridge.cs', exe, 'BRIDGE_TEST')
        compile_cs(ROOT / 'tests/MockEngine.cs', app / 'bin/yt-dlp/yt-dlp.exe')
        (app / 'bin/ffmpeg/ffmpeg.exe').write_bytes(b'offline placeholder')
        env = dict(os.environ, SG_TEST_APP=str(app))
        def config(mode='off', proxy='', **kwargs):
            settings = {'download_dir': str(downloads), 'proxy_mode': mode, 'proxy_url': proxy}
            settings.update(kwargs)
            (app / 'config.json').write_text(json.dumps(settings), encoding='utf-8')
        config()
        assert request(exe, {'action': 'ping'}, env)['engineAvailable']
        (app / 'bin/ffmpeg/ffmpeg.exe').unlink()
        assert request(exe, {'action': 'ping'}, env)['engineAvailable'] is False
        (app / 'bin/ffmpeg/ffmpeg.exe').write_bytes(b'offline placeholder')
        checks += 2
        for url in ['http://x.com/test/status/123', 'https://x.com.evil.invalid/test/status/123',
                    'https://user:password@x.com/test/status/123', 'https://x.com:8443/test/status/123',
                    'https://x.com/test', 'https://x.com/test/status/123/extra', 'https://youtube.com/watch?v=abcdefghijk']:
            assert request(exe, {'action': 'x-download', 'url': url}, env)['ok'] is False
            checks += 1
        assert request(exe, {'action': 'x-download', 'url': 'https://x.com/test/status/123', 'quality': 'invalid'}, env)['ok'] is False
        checks += 1

        def accept(number, quality='best', url=None):
            response = request(exe, {'action': 'x-download', 'url': url or f'https://x.com/test/status/{number}', 'quality': quality}, env)
            assert response['ok'], 'valid fixture job rejected'
            assert len(response['jobId']) == 32
            return response
        def finish(job):
            deadline = time.monotonic() + 12
            observed_progress = False
            while time.monotonic() < deadline:
                response = request(exe, {'action': 'job-status', 'jobId': job['jobId']}, env)
                assert response.get('ok') is True, 'job-status failed: ' + json.dumps(response, ensure_ascii=True)
                state = response['job']
                if state['state'] == 'downloading' and state['percent'] > 0:
                    observed_progress = True
                if state['state'] in ['completed', 'failed']:
                    deadline_read = time.monotonic() + 2
                    while True:
                        try:
                            persisted = json.loads((host_dir / 'jobs' / (job['jobId'] + '.json')).read_text(encoding='utf-8'))
                            break
                        except PermissionError:
                            if time.monotonic() >= deadline_read:
                                raise
                            time.sleep(.02)
                    assert 'proxy' not in persisted and 'cookieFile' not in persisted
                    assert not (host_dir / 'jobs' / (job['jobId'] + '.cookies.txt')).exists()
                    return state, observed_progress
                time.sleep(.05)
            raise AssertionError('worker did not settle')

        # Synthetic credentials only; any attempted network access would hit an inert mock executable.
        cookie = app / 'bin/cookies_twitter.txt'
        cookie.write_text('# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tmock-token\n.x.com\tTRUE\t/\tTRUE\t0\tct0\tmock-ct0\n', encoding='utf-8')
        config('http', '127.0.0.1:7897', proxy_enabled=False)
        job = accept('101', '720', 'https://mobile.twitter.com/test/status/101/video/1?s=20')
        assert job['cookieStatus'] == 'signed-in'
        duplicate = accept('101', '720')
        if duplicate['jobId'] != job['jobId']:
            diagnostic = host_dir / 'jobs' / (job['jobId'] + '.test-error.txt')
            if diagnostic.exists():
                print(diagnostic.read_text(encoding='utf-8'))
            response = request(exe, {'action': 'job-status', 'jobId': job['jobId']}, env)
            raise AssertionError('duplicate job spawned; first response=' + json.dumps(response, ensure_ascii=True))
        state, progress = finish(job)
        assert state['state'] == 'completed' and state['percent'] == 100 and progress
        assert not list((host_dir / 'jobs').glob('*.cookies.txt'))
        args = json.loads((app / 'invocation.json').read_text())
        assert args.count('--proxy') == 1 and args[args.index('--proxy') + 1] == 'http://127.0.0.1:7897'
        assert args[args.index('--format') + 1] == 'bv*[height<=720]+ba/b[height<=720]'
        assert args[-2:] == ['--', 'https://x.com/test/status/101']
        assert '--ignore-config' in args and '--no-playlist' in args and '--cookies' in args and '--progress' in args
        checks += 7

        cookie.write_text('# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tTRUE\t0\tguest_id\tmock-guest\n', encoding='utf-8')
        config('off', '127.0.0.1:7897')
        guest = accept('102')
        guest_state, guest_progress = finish(guest)
        assert guest['cookieStatus'] == 'guest' and guest_state['state'] == 'completed' and guest_progress
        args = json.loads((app / 'invocation.json').read_text())
        assert '--cookies' not in args and args[args.index('--proxy') + 1] == ''
        checks += 2
        for number, expected in [('9998', 'X 要求登录'), ('9997', '没有可下载的视频'), ('9999', '视频不可访问')]:
            state, _ = finish(accept(number))
            assert state['state'] == 'failed' and expected in state['error']
            assert 'mock-sensitive' not in state['error']
            checks += 1
        # Valid JSON without a running worker should be marked failed after its grace period.
        stale = 'f' * 32
        ticks = 621355968000000000 + int((time.time() - 120) * 10_000_000)
        (host_dir / 'jobs' / (stale + '.json')).write_text(json.dumps({'id': stale, 'state': 'downloading', 'percent': 25,
            'updated': ticks, 'pid': 0, 'downloadDir': str(downloads)}), encoding='utf-8')
        assert request(exe, {'action': 'job-status', 'jobId': stale}, env)['job']['state'] == 'failed'
        checks += 1
    print(f'Native bridge offline tests: {checks} checks passed; framing, validation, detached jobs, progress, format, proxy, Cookie isolation and failure reporting verified.')


if __name__ == '__main__':
    main()
