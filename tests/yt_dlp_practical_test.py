"""Real bundled yt-dlp/FFmpeg smoke test using only locally generated media."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    desktop = Path.home() / 'Desktop'
    roots = [Path(os.environ['VEYLO_TEST_FLUENT_DIR'])] if os.environ.get('VEYLO_TEST_FLUENT_DIR') else [desktop / 'FluentYTDL-3.7.6-win64-full']
    for category in [desktop / 'Applications']:
        roots.extend(category.glob('FluentYTDL-*-win64-full'))
    app = next(root for root in roots if (root / 'bin/yt-dlp/yt-dlp.exe').is_file())
    engine = app / 'bin/yt-dlp/yt-dlp.exe'
    ffmpeg = app / 'bin/ffmpeg/ffmpeg.exe'
    ffprobe = app / 'bin/ffmpeg/ffprobe.exe'

    def run(args):
        result = subprocess.run([str(arg) for arg in args], capture_output=True, encoding='utf-8', errors='replace', timeout=60)
        assert result.returncode == 0, (args[0], result.returncode, result.stderr[-2000:])
        return result.stdout

    with tempfile.TemporaryDirectory(prefix='veylo_local_practical_') as temp:
        root = Path(temp)
        source = root / 'source.mp4'
        image = root / 'cover.jpg'
        run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=10',
             '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '6', '-c:v', 'libx264', '-c:a', 'aac', str(source)])
        run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90', '-frames:v', '1', '-update', '1', str(image)])
        info = root / 'info.json'
        info.write_text(json.dumps({'id': 'localfixture', 'title': 'Local chapter test', 'ext': 'mp4',
                                   'url': source.as_uri(), 'extractor': 'generic', 'extractor_key': 'Generic',
                                   'webpage_url': 'https://example.invalid/localfixture',
                                   'thumbnails': [{'url': image.as_uri(), 'id': 'cover'}],
                                   'chapters': [{'start_time': 0, 'end_time': 3, 'title': 'First'},
                                                {'start_time': 3, 'end_time': 6, 'title': 'Second'}]}), encoding='utf-8')
        output = root / 'output'; output.mkdir()
        common = [engine, '--ignore-config', '--no-cache-dir', '--enable-file-urls', '--no-simulate', '--no-quiet',
                  '--ffmpeg-location', ffmpeg.parent, '--load-info-json', info, '--paths', output,
                  '--output', '%(title)s [%(id)s].%(ext)s', '--output', 'chapter:' + str(output / 'chapters/%(section_number)03d - %(section_title)s.%(ext)s'),
                  '--embed-thumbnail', '--convert-thumbnails', 'jpg', '--split-chapters', '--download-archive', root / 'archive.txt',
                  '--limit-rate', '10M', '--concurrent-fragments', '4',
                  '--print', 'after_move:SGCHAPTERSJ:%(chapters.:.filepath)j']
        first = run(common)
        marker = next(line for line in first.splitlines() if line.startswith('SGCHAPTERSJ:'))
        chapters = json.loads(marker[len('SGCHAPTERSJ:'):])
        assert len(chapters) == 2 and all(Path(path).is_file() for path in chapters), marker
        media = next(output.glob('*.mp4'))
        probe = json.loads(run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', media]))
        assert any(stream.get('disposition', {}).get('attached_pic') == 1 for stream in probe['streams']), 'cover absent'
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in output.rglob('*') if p.is_file()}
        second = run(common)
        assert 'has already been recorded in the archive' in second, second
        assert before == {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in output.rglob('*') if p.is_file()}, 'repeat modified media'
        print('PASS real bundled yt-dlp: embedded cover, two chapter files, chapter markers, archive skip, transfer flags; local files only')


if __name__ == '__main__':
    main()
