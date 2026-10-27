"""Exercise the real HTTP transfer, range validation, fallback and cancellation."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import random
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
try:
    import httpx
except ImportError:
    import os
    tool_root = os.environ.get("VEYLO_TEST_BILI_DIR")
    if not tool_root:
        raise SystemExit("Install httpx or set VEYLO_TEST_BILI_DIR to your Bili23 installation.")
    sys.path.insert(0, str(Path(tool_root) / "site-packages"))
    import httpx

DATA = random.Random(42).randbytes(16 * 1024 * 1024)

def load_driver(path):
    spec = importlib.util.spec_from_file_location("throughput_driver", path)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver


@contextmanager
def client_module(client):
    names = ("util", "util.network", "util.network.request")
    old = {name: sys.modules.get(name) for name in names}
    for name in names:
        sys.modules[name] = ModuleType(name)
    sys.modules["util.network.request"].get_client = lambda: client
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value



DRIVER = load_driver(ROOT / "bilibili/driver/bd_driver.py")


class TransferTests(unittest.TestCase):
    @contextmanager
    def fixture(self, mode="normal", existing=0, workers=4):
        ranges = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def handle(self):
                try:
                    super().handle()
                except (ConnectionError, OSError):
                    pass
            def do_GET(self):
                raw = self.headers.get("Range", "")
                ranges.append(raw)
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", raw)
                if mode == "ignore":
                    match = None
                start, end = (int(match[1]), int(match[2] or len(DATA) - 1)) if match else (0, len(DATA) - 1)
                self.send_response(206 if match else 200)
                self.send_header("Content-Length", str(end - start + 1))
                if match:
                    actual_start = start + 1 if mode == "wrong_range" else start
                    self.send_header("Content-Range", f"bytes {actual_start}-{end}/{len(DATA)}")
                self.end_headers()
                try:
                    for offset in range(start, end + 1, 65536):
                        self.wfile.write(DATA[offset:min(offset + 65536, end + 1)])
                        self.wfile.flush()
                        time.sleep(.0003)
                except (ConnectionError, OSError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="veylo_ranges_") as temp, httpx.Client(trust_env=False) as client, client_module(client), patch.object(DRIVER, "transfer_workers", return_value=workers):
                root = Path(temp)
                target = root / "media.bin"
                if existing:
                    target.write_bytes(DATA[:existing])
                record = root / "job.json"
                record.write_text(json.dumps({"id": "a" * 32, "state": "downloading"}), encoding="utf-8")
                job = DRIVER.Job(record)
                entry = {"url": f"http://127.0.0.1:{server.server_port}/media", "file_name": target.name, "file_size": len(DATA)}
                yield root, target, job, entry, ranges
        finally:
            server.shutdown()
            server.server_close()

    def verify(self, mode="normal", existing=0, workers=4, unknown=False):
        with self.fixture(mode, existing, workers) as (root, target, job, entry, ranges):
            deltas = []
            threads = []
            if unknown:
                entry["file_size"] = 0
            def progress(delta):
                deltas.append(delta)
                threads.append(threading.get_ident())
                job.save(force=False, percent=50)
            DRIVER.download_entry(entry, root, job, progress)
            self.assertEqual(hashlib.sha256(target.read_bytes()).digest(), hashlib.sha256(DATA).digest())
            self.assertEqual(sum(deltas), len(DATA) - existing)
            self.assertEqual(set(threads), {threading.get_ident()})
            self.assertFalse(list(root.glob(".veylo-range-*")))
            self.assertIsNone(json.loads(job.path.read_text())["speedBytesPerSecond"])
            return ranges

    def test_parallel_ranges_match_every_source_byte(self):
        ranges = self.verify()
        self.assertEqual(len(ranges), 4)
        self.assertTrue(all(value.startswith("bytes=") for value in ranges))

    def test_ignored_ranges_fall_back_to_whole_file(self):
        self.assertIn("", self.verify("ignore"))

    def test_wrong_content_range_cannot_publish_corrupt_file(self):
        self.assertIn("", self.verify("wrong_range"))

    def test_partial_existing_file_uses_serial_resume(self):
        self.assertEqual(self.verify(existing=512 * 1024), ["bytes=524288-"])

    def test_unknown_size_retains_serial_path(self):
        self.assertEqual(self.verify(unknown=True), [""])

    def test_single_thread_preference_is_preserved(self):
        self.assertEqual(self.verify(workers=1), [""])

    def test_cancel_discards_staging_and_never_falls_back(self):
        with self.fixture() as (root, target, job, entry, ranges):
            received = [0]
            def progress(delta):
                received[0] += delta
                if received[0] >= 1024 * 1024:
                    raise DRIVER.JobCanceled("test cancel")
            with self.assertRaises(DRIVER.JobCanceled):
                DRIVER.download_entry(entry, root, job, progress)
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob(".veylo-range-*")))
            self.assertNotIn("", ranges)


if __name__ == "__main__":
    unittest.main(verbosity=2)
