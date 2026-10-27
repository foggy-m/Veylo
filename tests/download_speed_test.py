"""Offline speed regression: real bridge methods and simulated streaming bytes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("speed_driver", ROOT / "bilibili/driver/bd_driver.py")
DRIVER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DRIVER)

PROBE = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
class SpeedProbe {
    static int checks;
    static void Check(bool value, string label) { if (!value) throw new Exception(label); checks++; }
    static void Main(string[] args) {
        var type = Assembly.LoadFrom(args[0]).GetType(args[1]);
        var flags = BindingFlags.NonPublic | BindingFlags.Static;
        var speed = type.GetMethod("DownloadSpeed", flags);
        var job = new Dictionary<string, object> { {"id", new string('a', 32)}, {"state", "downloading"},
            {"speedBytesPerSecond", 2048.0}, {"speedUpdated", DateTime.UtcNow.Ticks} };
        Func<object> read = () => speed.Invoke(null, new object[] {job});
        Check((double)read() == 2048, "fresh speed");
        foreach (var value in new object[] {0, 123L, 0.5, 2048m}) {
            job["speedBytesPerSecond"] = value;
            Check((double)read() == Convert.ToDouble(value), "numeric speed");
        }
        foreach (var value in new object[] {null, "2048", -1.0, double.NaN, double.PositiveInfinity}) {
            job["speedBytesPerSecond"] = value;
            Check(read() == null, "invalid speed");
        }
        job["speedBytesPerSecond"] = 2048.0;
        foreach (string state in new[] {"queued", "completed", "failed", "canceled"}) {
            job["state"] = state; Check(read() == null, "inactive speed");
        }
        job["state"] = "downloading";
        job["speedUpdated"] = DateTime.UtcNow.AddSeconds(-6).Ticks;
        Check(read() == null, "stale speed");
        job["speedUpdated"] = DateTime.UtcNow.AddSeconds(6).Ticks;
        Check(read() == null, "future timestamp");
        job.Remove("speedUpdated"); Check(read() == null, "legacy record");
        var update = type.GetMethod("UpdateProgress", flags);
        if (update != null) {
            Directory.CreateDirectory(Path.Combine(Path.GetDirectoryName(args[0]), "jobs"));
            Action<string, bool> feed = (line, playlist) => {
                Check((bool)update.Invoke(null, new object[] {job, line, playlist}), "recognized progress");
            };
            feed("SGPROGRESS:25.0%|SPEED:2.048e3|STATUS:downloading", false);
            Check((double)read() == 2048 && (double)job["percent"] == 25, "single progress/speed");
            feed("SGPROGRESS:2/4:50.0%|SPEED:4096|STATUS:downloading", true);
            Check((double)read() == 4096 && (double)job["percent"] == 37.5, "playlist progress/speed");
            feed("SGPROGRESS:NA|SPEED:512|STATUS:downloading", false);
            Check((double)read() == 512, "unknown percentage still has speed");
            foreach (string line in new[] {"SGPROGRESS:30%", "SGPROGRESS:30%|SPEED:NA|STATUS:downloading",
                "SGPROGRESS:30%|SPEED:-1|STATUS:downloading", "SGPROGRESS:30%|SPEED:NaN|STATUS:downloading",
                "SGPROGRESS:30%|SPEED:2048|STATUS:finished", "SGPOSTPROCESS"}) {
                feed(line, false); Check(read() == null, "reset unavailable/finished/postprocess speed");
            }
        }
        var concurrency = type.GetMethod("TransferConcurrency", flags);
        if (concurrency != null) {
            var config = new Dictionary<string, object>();
            Func<int> count = () => (int)concurrency.Invoke(null, new object[] {job, config});
            Check(count() == 4, "auto concurrency fallback");
            config["concurrent_fragments"] = 8;
            Check(count() == 8, "auto inherits FluentYTDL");
            job["concurrentFragments"] = "1";
            Check(count() == 1, "explicit single connection preserved");
            job["concurrentFragments"] = "4";
            Check(count() == 4, "explicit concurrency overrides config");
            job["concurrentFragments"] = "auto";
            config["concurrent_fragments"] = 99;
            Check(count() == 16, "configured concurrency bounded");
            foreach (object value in new object[] {null, -1, 0, "8", true}) {
                config["concurrent_fragments"] = value;
                Check(count() == 4, "invalid config falls back safely");
            }
        }
        Console.WriteLine(args[1] + ": " + checks + " speed assertions passed");
    }
}
'''


class SpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="veylo_speed_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "job.json"
        self.path.write_text(json.dumps({"id": "a" * 32, "state": "downloading"}), encoding="utf-8")
        self.clock = [100.0]
        self.monotonic = patch.object(DRIVER.time, "monotonic", side_effect=lambda: self.clock[0])
        self.monotonic.start()
        self.addCleanup(self.monotonic.stop)
        self.job = DRIVER.Job(self.path)

    def test_sampling_uses_received_bytes_and_resets_between_streams(self):
        self.job.start_transfer()
        self.clock[0] += .5
        self.job.transfer_progress(1024)
        self.assertIsNone(self.job.data["speedBytesPerSecond"])
        self.clock[0] += .5
        self.job.transfer_progress(1024)
        self.job.save(force=False, percent=5)
        self.assertEqual(json.loads(self.path.read_text())["speedBytesPerSecond"], 2048)
        self.clock[0] += 2
        self.job.transfer_progress(1024)
        self.assertEqual(self.job.data["speedBytesPerSecond"], 512)
        self.job.stop_transfer()
        self.assertIsNone(json.loads(self.path.read_text())["speedBytesPerSecond"])
        self.clock[0] += 100
        self.job.start_transfer()
        self.clock[0] += 1
        self.job.transfer_progress(1024)
        self.assertEqual(self.job.data["speedBytesPerSecond"], 1024)
        self.job.save(state="completed")
        self.assertIsNone(json.loads(self.path.read_text())["speedBytesPerSecond"])

    def test_stream_counts_bytes_and_clears_speed_before_merge_or_retry(self):
        for fail in (False, True):
            snapshots = []
            response = SimpleNamespace(status_code=200, raise_for_status=lambda: None)
            def chunks(size):
                self.assertEqual(size, DRIVER.CHUNK)
                for _ in range(2):
                    self.clock[0] += 1
                    yield b"x" * 1024
                if fail:
                    raise RuntimeError("connection failed")
            response.iter_bytes = chunks
            from contextlib import nullcontext
            client = SimpleNamespace(stream=lambda *a, **k: nullcontext(response))
            modules = {name: ModuleType(name) for name in ("httpx", "util", "util.network", "util.network.request")}
            modules["util.network.request"].get_client = lambda: client
            def progress(delta):
                self.job.save(force=False, percent=10)
                snapshots.append(json.loads(self.path.read_text())["speedBytesPerSecond"])
            with patch.dict(sys.modules, modules), patch.object(DRIVER.time, "sleep"):
                entry = {"file_name": "media.bin", "file_size": 2048, "url": "offline://fixture"}
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "connection failed"):
                        DRIVER.download_entry(entry, self.root, self.job, progress)
                else:
                    DRIVER.download_entry(entry, self.root, self.job, progress)
            self.assertTrue(snapshots and all(speed == 1024 for speed in snapshots))
            self.assertIsNone(json.loads(self.path.read_text())["speedBytesPerSecond"])
            self.assertEqual((self.root / "media.bin").stat().st_size, 2048)

    def test_compiled_bridge_validation_and_progress_parser(self):
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        source = self.root / "SpeedProbe.cs"
        source.write_text(PROBE, encoding="utf-8")
        probe = self.root / "SpeedProbe.exe"
        subprocess.run([str(compiler), "/nologo", "/out:" + str(probe), str(source)], check=True, capture_output=True)
        for name, cls in (("ShiguangBridge.exe", "ShiguangBridge"), ("BiliBridge.exe", "BiliBridge")):
            target = self.root / name
            source = ROOT / ("native/ShiguangBridge.cs" if cls == "ShiguangBridge" else "bilibili/native/BiliBridge.cs")
            subprocess.run([str(compiler), "/nologo", "/r:System.Windows.Forms.dll", "/r:System.Web.Extensions.dll",
                            "/out:" + str(target), str(source)], check=True, capture_output=True)
            result = subprocess.run([str(probe), str(target), cls], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
