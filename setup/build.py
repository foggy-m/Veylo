"""Build the standalone Setup EXE from an explicit public runtime allowlist."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'setup'
DIST = ROOT / 'dist'
VERSION = '2.4.5'
OUTPUT = DIST / f'Veylo-{VERSION}-Setup-x64.exe'
COMPILER = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
RUNTIME = {
    'extension/assets/brand.svg', 'extension/background.js', 'extension/icons.js',
    'extension/manifest.json', 'extension/offscreen.html', 'extension/offscreen.js',
    'extension/popup.css', 'extension/popup.html', 'extension/popup.js',
    'extension/shared.js', 'extension/ui.css', 'extension/ui.js',
    'extension/icons/icon16.png', 'extension/icons/icon32.png', 'extension/icons/icon48.png',
    'extension/icons/icon64.png', 'extension/icons/icon128.png', 'extension/icons/logo.png',
    'bilibili/driver/bd_driver.py', 'setup/GETTING-STARTED.txt',
}


def compile_exe(target, files, switches=()):
    subprocess.run([str(COMPILER), '/nologo', '/codepage:65001', '/optimize+', '/out:' + str(target),
                    '/r:System.Windows.Forms.dll', '/r:System.Web.Extensions.dll', *switches,
                    *map(str, files)], cwd=ROOT, check=True, timeout=45)


def create_icon(target):
    images = [(size, (ROOT / f'extension/icons/icon{size}.png').read_bytes()) for size in (16, 32, 48, 128)]
    header = struct.pack('<HHH', 0, 1, len(images))
    directory, offset = b'', len(header) + 16 * len(images)
    for size, data in images:
        directory += struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    target.write_bytes(header + directory + b''.join(data for _, data in images))


def payload_files():
    # Use the recorded icon inventory instead of walking the user's directories.
    lucide = ROOT / 'extension/third_party/lucide'
    source = json.loads((lucide / 'SOURCE.json').read_text(encoding='utf-8'))
    if not all(re.fullmatch(r'[a-z0-9-]+', name) for name in source['icons']):
        raise ValueError('Invalid Lucide icon inventory')
    names = [name + '.svg' for name in source['icons']]
    files = RUNTIME | {'extension/third_party/lucide/' + name for name in [*names, 'LICENSE', 'SOURCE.json']}
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    if not files.issubset(tracked):
        raise ValueError('The installer payload must be a subset of the public source tree')
    payload = {}
    for relative in sorted(files):
        path = ROOT / relative
        if not path.is_file() or path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Not a regular source file: ' + relative)
        payload[relative.removeprefix('setup/')] = path.read_bytes()
    return payload


def inspect_payload(payload):
    sys.path.insert(0, str(ROOT / 'tools'))
    from check_publication import PATTERNS
    compiled = dict(PATTERNS)
    for path, data in payload.items():
        if path.endswith('.exe'):
            # .NET string constants use UTF-16LE and can be aligned on either byte offset.
            views = (data, data.decode('utf-16le', 'ignore').encode(), data[1:].decode('utf-16le', 'ignore').encode())
        else:
            views = (data,)
        for category, pattern in compiled.items():
            if any(re.search(pattern, view) is not None for view in views):
                raise ValueError(f'Unexpected {category} in {path}')
    manifest = json.loads(payload['extension/manifest.json'])
    if manifest['version'] != VERSION or any(manifest.get(key) for key in (
            'content_scripts', 'host_permissions', 'web_accessible_resources', 'externally_connectable')):
        raise ValueError('Unexpected manifest privileges or version')


def main():
    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='veylo-build-') as temporary:
        stage = Path(temporary)
        payload = payload_files()
        for relative in ('native/ShiguangBridge.cs', 'bilibili/native/BiliBridge.cs'):
            target = stage / Path(relative).with_suffix('.exe').name
            compile_exe(target, [ROOT / relative], ['/target:exe'])
            payload[relative.removesuffix('.cs') + '.exe'] = target.read_bytes()
        inspect_payload(payload)
        package = stage / 'payload.zip'
        with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            for relative, data in sorted(payload.items()):
                entry = zipfile.ZipInfo(relative, date_time=(2026, 11, 21, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                handle.writestr(entry, data)
        with zipfile.ZipFile(package) as handle:
            assert handle.testzip() is None and set(handle.namelist()) == set(payload)
            assert all(handle.read(relative) == data for relative, data in payload.items())
        icon = stage / 'veylo.ico'
        create_icon(icon)
        target = stage / OUTPUT.name
        compile_exe(target, sorted(HERE.glob('*.cs')), [
            '/target:winexe', '/platform:x64', '/r:System.Drawing.dll', '/r:System.Net.Http.dll',
            '/r:System.IO.Compression.dll', '/win32manifest:' + str(HERE / 'Setup.manifest'), '/win32icon:' + str(icon),
            '/resource:' + str(package) + ',veylo.payload.zip',
            '/resource:' + str(HERE / 'dependencies.json') + ',veylo.dependencies.json',
        ])
        binary = target.read_bytes()
        OUTPUT.write_bytes(binary)
    digest = hashlib.sha256(binary).hexdigest()
    (DIST / 'SHA256SUMS.txt').write_text(f'{digest}  {OUTPUT.name}\n', encoding='ascii')
    print(json.dumps({'file': OUTPUT.name, 'bytes': len(binary), 'sha256': digest, 'embeddedFiles': len(payload)}))


if __name__ == '__main__':
    main()
