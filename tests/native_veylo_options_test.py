"""Offline Veylo native protocol tests. Only unique temporary fixtures are changed."""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
ORIGIN = "chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/"
CHECKS = 0


def check(condition, label):
    global CHECKS
    assert condition, label
    CHECKS += 1


def compile_cs(source, output, test=False):
    args = [str(CSC), "/nologo", "/target:exe", "/r:System.Windows.Forms.dll",
            "/r:System.Web.Extensions.dll", "/out:" + str(output)]
    if test:
        args.append("/define:BRIDGE_TEST")
    result = subprocess.run(args + [str(source)], capture_output=True, timeout=45)
    print("COMPILE", output.name, "BRIDGE_TEST" if test else "release", "exit", result.returncode)
    if result.stdout.strip():
        print(result.stdout.decode("utf-8", "replace").strip())
    if result.stderr.strip():
        print(result.stderr.decode("utf-8", "replace").strip())
    check(result.returncode == 0, "C# compilation")


def frame(message):
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def exchange(exe, messages, env, origin=ORIGIN):
    args = [str(exe)] + ([origin] if origin else [])
    result = subprocess.run(args, input=b"".join(frame(m) for m in messages),
                            env=env, capture_output=True, timeout=15)
    check(result.returncode == 0 and not result.stderr, "native host exit/stdout framing")
    data = result.stdout
    responses = []
    while data:
        check(len(data) >= 4, "response header")
        size = struct.unpack("<I", data[:4])[0]
        check(0 < size <= 1048576 and len(data) >= size + 4, "response length")
        responses.append(json.loads(data[4:4 + size]))
        data = data[4 + size:]
    check(len(responses) == len(messages), "response count")
    return responses


def call(exe, message, env):
    return exchange(exe, [message], env)[0]


def wait(exe, accepted, env):
    check(accepted.get("ok") is True, "accept fixture")
    deadline = time.monotonic() + 15
    progress = False
    while time.monotonic() < deadline:
        status = call(exe, {"action": "job-status", "jobId": accepted["jobId"]}, env)
        check(status.get("ok") is True, "job-status fixture: " + repr(status))
        job = status["job"]
        progress |= job["state"] == "downloading" and 0 < job["percent"] < 100
        if job["state"] in ("completed", "failed", "canceled"):
            time.sleep(.08)  # Let the detached worker flush its final record and exit.
            return job, progress
        time.sleep(.08)
    raise AssertionError("isolated worker did not finish")


MOCK = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;
internal static class OfflineEngine {
    static string Option(string[] args, string key) {
        int i = Array.IndexOf(args, key); return i >= 0 ? args[i + 1] : "";
    }
    static string TempPath(string[] args) {
        for (int i = 0; i < args.Length - 1; i++)
            if (args[i] == "--paths" && args[i + 1].StartsWith("temp:")) return args[i + 1].Substring(5);
        return Option(args, "--paths");
    }
    // Long-running engine: records its PID, spawns a grandchild holding the
    // inherited output pipes (like yt-dlp -> ffmpeg) and waits to be killed.
    static int Hold(string app) {
        File.WriteAllText(Path.Combine(app, "engine.pid"), Process.GetCurrentProcess().Id.ToString());
        string childPid = Path.Combine(app, "child.pid");
        Process.Start(new ProcessStartInfo(typeof(OfflineEngine).Assembly.Location, "--sg-child \"" + childPid + "\"") { UseShellExecute = false, CreateNoWindow = true });
        for (int i = 0; i < 100 && !File.Exists(childPid); i++) Thread.Sleep(50);
        Console.WriteLine("SGPROGRESS:33.3%"); Console.Out.Flush();
        for (int i = 0; i < 600; i++) Thread.Sleep(100);
        return 0;
    }
    static string VideoId(string url) {
        int v = url.IndexOf("v=");
        if (v >= 0) return url.Substring(v + 2, 11);
        return url.Substring(url.LastIndexOf('/') + 1);
    }
    static int Main(string[] args) {
        if (args.Length == 2 && args[0] == "--sg-child") {
            File.WriteAllText(args[1] + ".tmp", Process.GetCurrentProcess().Id.ToString());
            File.Move(args[1] + ".tmp", args[1]);
            Thread.Sleep(120000); return 0;
        }
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        var json = new JavaScriptSerializer();
        string app = Environment.GetEnvironmentVariable("SG_TEST_APP");
        File.WriteAllText(Path.Combine(app, "invocation.json"), json.Serialize(args));
        string url = args.Last();
        string mode0 = Environment.GetEnvironmentVariable("SG_TEST_MODE") ?? "";
        string root = Option(args, "--paths");
        string archive = Option(args, "--download-archive");
        string archiveId = (url.Contains("youtube.com") ? "youtube " : "twitter ") + VideoId(url);
        if (archive != "" && File.ReadAllLines(archive).Contains(archiveId)) {
            Console.WriteLine("[download] " + VideoId(url) + ": has already been recorded in the archive"); return 0;
        }
        string ext0 = Option(args, "--audio-format"); if (ext0 == "") ext0 = "mp4";
        if (args.Contains("--live-from-start")) {
            string id = VideoId(url);
            if (mode0 != "live-empty") {
                string temporary = TempPath(args);
                File.WriteAllText(Path.Combine(temporary, "直播 [" + id + "]." + ext0 + ".part"), "synthetic live recording");
                File.WriteAllText(Path.Combine(temporary, "直播 [" + id + "]." + ext0 + ".ytdl"), "state");
                File.WriteAllText(Path.Combine(temporary, "直播 [" + id + "]." + ext0 + ".part-Frag7.part"), "fragment");
            }
            Console.WriteLine("SGTITLEJ:" + json.Serialize("合成直播"));
            return Hold(app);
        }
        if (args.Contains("--yes-playlist")) {
            int n = int.Parse(Environment.GetEnvironmentVariable("SG_TEST_ITEMS") ?? "3");
            string folder = Path.Combine(root, "合成列表");
            string playlistTitle = mode0 == "playlist-unsafe-title" ? "Lecture: Part 1" : "合成列表";
            Directory.CreateDirectory(folder);
            if (mode0 == "playlist-legacy-progress") {
                Console.WriteLine("SGPROGRESS:  12.5%"); Console.Out.Flush(); Thread.Sleep(650);
                Console.Error.WriteLine("SGPROGRESS:  42.0%"); Console.Error.Flush(); Thread.Sleep(650);
            }
            for (int i = 1; i <= n; i++) {
                string iid = "plitem" + i.ToString("D5");
                if (args.Contains("before_dl:SGIDJ:%(id)j")) Console.WriteLine("SGIDJ:" + json.Serialize(iid));
                if (mode0 != "playlist-legacy-progress") Console.WriteLine("SGPROGRESS:" + i + "/" + n + ":  50.0%"); Console.Out.Flush();
                if (mode0 == "playlist-progress" && i == 2) Thread.Sleep(1200);
                if (mode0 == "playlist-slow" && i == 2) {
                    string temporary = Path.Combine(TempPath(args), "合成列表"); Directory.CreateDirectory(temporary);
                    File.WriteAllText(Path.Combine(temporary, "002 - 条目 2 [" + iid + "]." + ext0 + ".part"), "partial");
                    return Hold(app);
                }
                if ((mode0 == "playlist-partial" && i == 2) || mode0 == "playlist-allfail") {
                    Console.Error.WriteLine("ERROR: synthetic-private-value item failed"); continue;
                }
                string item = Path.Combine(folder, i.ToString("D3") + " - 条目 " + i + " [" + iid + "]." + ext0);
                File.WriteAllText(item, "synthetic media " + i);
                Console.WriteLine("SGFILEJ:" + json.Serialize(item));
                Console.WriteLine("SGPLTITLEJ:" + json.Serialize(playlistTitle));
            }
            Console.WriteLine("SGPLTITLEJ:" + json.Serialize(playlistTitle));
            return mode0 == "playlist-partial" || mode0 == "playlist-allfail" ? 1 : 0;
        }
        if (mode0 == "same-id-slow") {
            string id = VideoId(url);
            string part = Path.Combine(TempPath(args), "B [" + id + "].f248.webm.part");
            File.WriteAllText(part, "other active same-id job");
            File.WriteAllText(Path.Combine(app, "same-id-part.txt"), part);
            return Hold(app);
        }
        if (mode0 == "slow") {
            string id = VideoId(url);
            string temporary = TempPath(args);
            File.WriteAllText(Path.Combine(temporary, "合成 [" + id + "].f137.mp4.part"), "partial");
            File.WriteAllText(Path.Combine(temporary, "合成 [" + id + "].f137.mp4.ytdl"), "state");
            File.WriteAllText(Path.Combine(temporary, "合成 [" + id + "].temp.mp4"), "postprocessor temp");
            File.WriteAllText(Path.Combine(root, "并发 [zzzzzzzzzzz].mp4.part"), "concurrent unrelated job");
            return Hold(app);
        }
        Console.WriteLine("SGPROGRESS:21.5%"); Console.Out.Flush();
        Console.Error.WriteLine("SGPROGRESS:42.0%"); Console.Error.Flush();
        Thread.Sleep(650);
        if (url.EndsWith("/9998")) { Console.Error.WriteLine("login required synthetic-private-value"); return 1; }
        if (url.EndsWith("/9997")) { Console.Error.WriteLine("No video found synthetic-private-value"); return 1; }
        if (url.EndsWith("/9999")) return 0;
        string ext = Option(args, "--audio-format");
        bool staged = !args.Contains("-x") && !args.Contains("--format");
        string file = Path.Combine(Option(args, "--paths"), staged ? "original, 音源 [clip].mp4"
            : "offline, 完成 " + Guid.NewGuid().ToString("N") + "." + (ext == "" ? "mp4" : ext));
        string mode = Environment.GetEnvironmentVariable("SG_TEST_MODE") ?? "";
        string fixture = Environment.GetEnvironmentVariable("SG_TEST_SOURCE");
        if (!string.IsNullOrEmpty(fixture)) { file = Path.ChangeExtension(file, Path.GetExtension(fixture)); File.Copy(fixture, file); }
        else File.WriteAllText(file, mode == "empty-source" ? "" : "synthetic media");
        if (args.Contains("--write-thumbnail") && mode != "missing-thumbnail") {
            string image = Path.ChangeExtension(file, ".jpg");
            string thumbnail = Environment.GetEnvironmentVariable("SG_TEST_THUMB");
            if (!string.IsNullOrEmpty(thumbnail)) File.Copy(thumbnail, image);
            else File.WriteAllText(image, "synthetic thumbnail");
        }
        if (args.Contains("--split-chapters")) {
            string chapterRoot = Path.Combine(root, "合成章节.chapters"); Directory.CreateDirectory(chapterRoot);
            string a = Path.Combine(chapterRoot, "001 - Intro.mp4"), b = Path.Combine(chapterRoot, "002 - Main.mp4");
            File.WriteAllText(a, "chapter one"); File.WriteAllText(b, "chapter two");
            Console.WriteLine("SGCHAPTERSJ:" + json.Serialize(new[] { a, b }));
        }
        if (mode == "outside-source") Console.WriteLine("SGFILEJ:" + json.Serialize(Path.Combine(app, "outside.mp4")));
        else if (mode == "traversal-source") Console.WriteLine("SGFILEJ:" + json.Serialize(Path.Combine(Option(args, "--paths"), "..", "..", "..", "outside.mp4")));
        else if (mode == "relative-source") Console.WriteLine("SGFILEJ:" + json.Serialize("original, 音源 [clip].mp4"));
        else if (mode == "bad-marker") Console.WriteLine("SGFILEJ:{not json}");
        else if (mode != "no-source") {
            if (url.EndsWith("/901")) Console.WriteLine("SGFILE:" + file);
            else if (!url.EndsWith("subonly0000")) Console.WriteLine("SGFILEJ:" + json.Serialize(file));
        }
        if (mode == "multi-source" || mode == "postprocess-second-hold") {
            string second = Path.Combine(Option(args, "--paths"), "second, 音源 [clip2].webm");
            File.WriteAllText(second, "second synthetic media");
            Console.WriteLine("SGFILEJ:" + json.Serialize(second));
            Console.WriteLine("SGFILEJ:" + json.Serialize(file));
        }
        if (mode == "download-fail") return 1;
        if (archive != "") File.AppendAllText(archive, archiveId + "\n");
        if (mode == "source-announced") { Console.Out.Flush(); Thread.Sleep(900); }
        Console.WriteLine("SGTITLEJ:" + json.Serialize("Veylo 合成标题\n第二行"));
        if (!staged) Console.WriteLine("SGFILEJ:" + json.Serialize(Path.Combine(app, "outside.mp4")));
        if (args.Contains("--write-subs")) {
            string sub = Path.ChangeExtension(file, ".en.srt"); File.WriteAllText(sub, "1\n00:00:00,000 --> 00:00:00,100\noffline\n");
            Console.WriteLine("SGSUBSJ:" + json.Serialize(new { en = new { filepath = sub } }));
        }
        return 0;
    }
}
'''


MOCK_FFMPEG = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;
internal static class OfflineEncoder {
    static int Main(string[] args) {
        if (args.Length == 2 && args[0] == "--sg-child") {
            File.WriteAllText(args[1], Process.GetCurrentProcess().Id.ToString()); Thread.Sleep(120000); return 0;
        }
        string app = Environment.GetEnvironmentVariable("SG_TEST_APP");
        File.AppendAllText(Path.Combine(app, "ffmpeg-invocations.jsonl"), new JavaScriptSerializer().Serialize(args) + "\n");
        string mode = Environment.GetEnvironmentVariable("SG_TEST_MODE") ?? "";
        if (mode == "postprocess-hold" || (mode == "postprocess-second-hold"
            && args.Any(a => a.EndsWith("second, 音源 [clip2].webm", StringComparison.Ordinal)))) {
            File.WriteAllText(Path.Combine(app, "postprocess.pid"), Process.GetCurrentProcess().Id.ToString());
            Process.Start(new ProcessStartInfo(typeof(OfflineEncoder).Assembly.Location, "--sg-child \"" + Path.Combine(app, "postprocess-child.pid") + "\"") { UseShellExecute = false, CreateNoWindow = true });
            for (int i = 0; i < 100 && !File.Exists(Path.Combine(app, "postprocess-child.pid")); i++) Thread.Sleep(20);
            File.WriteAllText(args.Last(), "unfinished encode must stay private");
            for (int i = 0; i < 600 && !File.Exists(Path.Combine(app, "postprocess.release")); i++) Thread.Sleep(100);
        }
        if (mode == "ffmpeg-missing") return 0;
        File.WriteAllText(args.Last(), mode == "ffmpeg-empty" ? "" : "encoded synthetic audio");
        if (mode == "ffmpeg-fail") return 1;
        return 0;
    }
}
'''


MOCK_FFPROBE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;
internal static class OfflineProbe {
    static int Main(string[] args) {
        if (args.Length == 2 && args[0] == "--sg-child") {
            File.WriteAllText(args[1], Process.GetCurrentProcess().Id.ToString()); Thread.Sleep(120000); return 0;
        }
        string app = Environment.GetEnvironmentVariable("SG_TEST_APP");
        var json = new JavaScriptSerializer();
        File.AppendAllText(Path.Combine(app, "ffprobe-invocations.jsonl"), json.Serialize(args) + "\n");
        string mode = Environment.GetEnvironmentVariable("SG_TEST_MODE") ?? "";
        if (mode == "probe-hold") {
            File.WriteAllText(Path.Combine(app, "postprocess.pid"), Process.GetCurrentProcess().Id.ToString());
            Process.Start(new ProcessStartInfo(typeof(OfflineProbe).Assembly.Location, "--sg-child \"" + Path.Combine(app, "postprocess-child.pid") + "\"") { UseShellExecute = false, CreateNoWindow = true });
            for (int i = 0; i < 100 && !File.Exists(Path.Combine(app, "postprocess-child.pid")); i++) Thread.Sleep(20);
            for (int i = 0; i < 600 && !File.Exists(Path.Combine(app, "postprocess.release")); i++) Thread.Sleep(100);
        }
        if (mode == "probe-fail") return 1;
        if (mode == "probe-json") { Console.WriteLine("not JSON synthetic-private-value"); return 0; }
        if (mode == "probe-no-audio") { Console.WriteLine("{\"streams\":[]}"); return 0; }
        Console.WriteLine(json.Serialize(new { streams = new[] { new { codec_name = Environment.GetEnvironmentVariable("SG_TEST_CODEC") ?? "aac" } } }));
        return 0;
    }
}
'''


class FixtureDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        # Never race a detached fixture worker, including when an assertion fails.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            active = False
            for file in (Path(self.name) / "native/jobs").glob("*.json"):
                try:
                    job = json.loads(file.read_text(encoding="utf-8"))
                    active |= job.get("state") in ("queued", "downloading") and job.get("pid", 0) != 0
                except (OSError, ValueError):
                    active = True
            if not active:
                break
            time.sleep(.15)
        for attempt in range(30):
            try:
                super().cleanup()
                return
            except PermissionError:
                time.sleep(.1)
        super().cleanup()


FEATURES = ["cancel", "sponsorblock", "sections", "playlist", "live", "pick-folder-initial", "login-status", "thumbnail", "chapters", "archive", "transfer", "open-file"]
STATUS_KEYS = {"id", "state", "percent", "downloadDir", "error", "platform", "url", "title", "media", "audioQuality", "files",
               "sponsorblock", "section", "playlist", "items", "live", "fileCount", "warnings"}
SB_ALL = "sponsor,selfpromo,interaction,intro,outro,preview,music_offtopic,filler"
PLAYLIST = "https://www.youtube.com/playlist?list=PLsynthetic0001"
TICKS_EPOCH = 621355968000000000


def alive(pid):
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, int(pid))  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT => still running
    finally:
        kernel.CloseHandle(handle)


def ticks(offset=0.0):
    return TICKS_EPOCH + int((time.time() + offset) * 10000000)


def queued_cancel_race_test(exe, env, app, native, downloads, base):
    jobs = native / "jobs"
    invocation_path = app / "invocation.json"
    before = invocation_path.read_bytes() if invocation_path.exists() else None
    job_id = uuid.uuid4().hex
    record = {"id": job_id, "url": base["url"], "quality": "best", "downloadDir": str(downloads), "state": "queued", "percent": 0.0,
              "pid": 0, "cookieStatus": "guest", "media": "video", "files": [], "created": ticks(), "updated": ticks(), "error": ""}
    job_path = jobs / (job_id + ".json")
    job_path.write_text(json.dumps(record), encoding="utf-8")
    reply = call(exe, {"action": "cancel-job", "jobId": job_id}, env)
    check(reply == {"ok": True, "jobId": job_id, "state": "canceling"}, "queued race cancel acknowledged")
    canceled = call(exe, {"action": "job-status", "jobId": job_id}, env)["job"]
    check(canceled["state"] == "canceled" and canceled["error"] == "已取消下载。" and not (jobs / (job_id + ".cancel")).exists(),
          "queued race status finalizes and removes marker before worker starts")
    result = subprocess.run([str(exe), "--worker", job_id], env=env, capture_output=True, timeout=20)
    check(result.returncode == 0, "delayed queued worker exits")
    final = call(exe, {"action": "job-status", "jobId": job_id}, env)["job"]
    check(final == canceled and (invocation_path.read_bytes() if invocation_path.exists() else None) == before,
          "late worker cannot revive canceled job or start engine after marker removal")
    print("PASS queued cancel status/late-worker race")


def playlist_audio_title_test(exe, env, downloads):
    request = {"action": "yt-download", "url": PLAYLIST, "playlist": True, "items": "1-3", "media": "audio-mp3", "audioQuality": "192"}
    for metadata_enabled in (False, True):
        accepted = call(exe, {**request, "metadata": metadata_enabled}, dict(env, SG_TEST_MODE="playlist-unsafe-title"))
        job, _ = wait(exe, accepted, env)
        check(job["state"] == "completed" and job["fileCount"] == 3 and job["title"] == "Lecture: Part 1",
              "playlist raw punctuation title completes staged audio while preserving display title")
        check(len(job["files"]) == 3 and all(Path(file).parent == downloads / "合成列表" and Path(file).suffix == ".mp3" for file in job["files"]),
              "playlist audio follows actual sanitized source subfolder, not raw title")
    print("PASS playlist audio sanitized folder/raw title")


def section_minutes_test(exe, env, app, base):
    for section, normalized in (("90:00-95:00", "*5400-5700"), ("1439:59-1440:00", "*86399-86400")):
        accepted = call(exe, {**base, "section": section}, env)
        check(accepted.get("ok") is True, "two-part minute range accepted: " + section)
        job, _ = wait(exe, accepted, env)
        args = json.loads((app / "invocation.json").read_text(encoding="utf-8"))
        check(job["state"] == "completed" and args[args.index("--download-sections") + 1] == normalized,
              "two-part minutes normalize to exact seconds: " + section)
    for invalid in ("1:60-2:00", "1:60:00-2:00:00", "1440:00-1440:01"):
        check(call(exe, {**base, "section": invalid}, env).get("ok") is False, "seconds/HH:MM minutes/24-hour limit remain strict: " + invalid)
    print("PASS MM:SS minutes beyond 59 with strict HH:MM:SS")


def live_part_ownership_test(exe, env, app, downloads):
    for name in ("engine.pid", "child.pid"):
        (app / name).unlink(missing_ok=True)
    live_env = dict(env, SG_TEST_MODE="live-empty", SG_TEST_LIVE_SECONDS="2")
    accepted = call(exe, {"action": "yt-download", "url": "https://www.youtube.com/watch?v=livevideo04", "live": "from-start"}, live_env)
    check(accepted.get("ok") is True, "empty live ownership fixture accepted")
    deadline = time.monotonic() + 8
    while not (app / "child.pid").exists():
        if time.monotonic() >= deadline:
            raise AssertionError("empty live fixture child did not start")
        time.sleep(.025)
    engine_pid = int((app / "engine.pid").read_text())
    child_pid = int((app / "child.pid").read_text())
    unrelated = downloads / "unrelated [zzzzzzzzzzz].mp4.part"
    content = b"other synthetic job live partial"
    unrelated.write_bytes(content)
    job, _ = wait(exe, accepted, env)
    check(unrelated.exists() and unrelated.read_bytes() == content and not unrelated.with_suffix("").exists(),
          "live cap never promotes or deletes another job's newly created part")
    check(job["state"] == "failed" and job["error"] == "直播录制已达到时长上限，未生成文件。" and job["files"] == [] and job["fileCount"] == 0,
          "live cap without owned output fails even if another job has a new part")
    deadline = time.monotonic() + 5
    while alive(engine_pid) or alive(child_pid):
        if time.monotonic() >= deadline:
            raise AssertionError("empty live ownership fixture tree did not stop")
        time.sleep(.025)
    print("PASS live cap part ownership and empty output")


def postprocess_cancel_test(exe, env, app, native, downloads):
    for mode, media, expected_count in (("postprocess-hold", "audio-mp3", 0), ("postprocess-second-hold", "audio-mp3", 1),
                                        ("probe-hold", "audio-original", 0)):
        for name in ("postprocess.pid", "postprocess-child.pid", "postprocess.release"):
            (app / name).unlink(missing_ok=True)
        accepted = call(exe, {"action": "yt-download", "url": "https://www.youtube.com/watch?v=postproc001", "media": media,
                              "audioQuality": "192"}, dict(env, SG_TEST_MODE=mode))
        check(accepted.get("ok") is True, "postprocess cancel accepted " + mode)
        deadline = time.monotonic() + 10
        while not (app / "postprocess-child.pid").exists():
            if time.monotonic() >= deadline:
                raise AssertionError("postprocess hold did not start " + mode)
            time.sleep(.025)
        parent_pid = int((app / "postprocess.pid").read_text())
        child_pid = int((app / "postprocess-child.pid").read_text())
        interim = call(exe, {"action": "job-status", "jobId": accepted["jobId"]}, env)["job"]
        canceled_tree = False
        try:
            reply = call(exe, {"action": "cancel-job", "jobId": accepted["jobId"]}, env)
            check(reply.get("state") == "canceling", "postprocess cancel acknowledged " + mode)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and (alive(parent_pid) or alive(child_pid)):
                time.sleep(.025)
            canceled_tree = not alive(parent_pid) and not alive(child_pid)
        finally:
            (app / "postprocess.release").write_bytes(b"release synthetic hold")
            if alive(child_pid):
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(child_pid), "/T", "/F"],
                               capture_output=True, timeout=10)
        final, _ = wait(exe, accepted, env)
        check(canceled_tree, "cancel stops direct encoder/probe and child PID tree before release " + mode)
        check(final["state"] == "canceled" and final["error"] == "已取消下载。" and final["percent"] == interim["percent"],
              "postprocess cancellation remains terminal without 100 percent " + mode)
        check(len(final["files"]) == expected_count and final["fileCount"] == expected_count
              and all(Path(file).exists() for file in final["files"]), "postprocess cancel keeps only earlier completed outputs " + mode)
        check(not (native / "jobs" / (accepted["jobId"] + ".cancel")).exists()
              and not list((native / "jobs").glob(accepted["jobId"] + ".audio-*")), "postprocess cancel removes marker and private stage " + mode)
    print("PASS direct encoder/probe cancel PID trees and completed outputs")


def same_id_temp_ownership_test(exe, env, app, downloads):
    for name in ("engine.pid", "child.pid", "same-id-part.txt"):
        (app / name).unlink(missing_ok=True)
    request = {"action": "yt-download", "url": "https://www.youtube.com/watch?v=samevideo01"}
    first = call(exe, {**request, "quality": "1080"}, dict(env, SG_TEST_MODE="slow"))
    deadline = time.monotonic() + 8
    while not (app / "child.pid").exists():
        if time.monotonic() >= deadline:
            raise AssertionError("first same-id job did not start")
        time.sleep(.025)
    second = call(exe, {**request, "quality": "720"}, dict(env, SG_TEST_MODE="same-id-slow"))
    deadline = time.monotonic() + 8
    while not (app / "same-id-part.txt").exists():
        if time.monotonic() >= deadline:
            raise AssertionError("second same-id job did not write its part")
        time.sleep(.025)
    part = Path((app / "same-id-part.txt").read_text())
    original = part.read_bytes()
    preserved = False
    try:
        call(exe, {"action": "cancel-job", "jobId": first["jobId"]}, env)
        canceled, _ = wait(exe, first, env)
        active = call(exe, {"action": "job-status", "jobId": second["jobId"]}, env)["job"]
        preserved = canceled["state"] == "canceled" and active["state"] == "downloading" and part.exists() and part.read_bytes() == original
    finally:
        call(exe, {"action": "cancel-job", "jobId": second["jobId"]}, env)
        wait(exe, second, env)
    check(preserved, "canceling one job never deletes active same-video-id different-quality job's partial")
    print("PASS concurrent same-ID per-job temporary ownership")


def feature_tests(exe, env, app, native, downloads, fixture, accept, invocation, base, other_dir):
    jobs = native / "jobs"

    def status(job_id):
        reply = call(exe, {"action": "job-status", "jobId": job_id}, env)
        check(reply.get("ok") is True and set(reply["job"]) == STATUS_KEYS, "job-status shape: " + repr(reply))
        return reply["job"]

    def job_count():
        return len(list(jobs.glob("*.json")))

    def wait_for(predicate, label, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.05)
        raise AssertionError("timed out: " + label)

    def arg(args, key):
        return args[args.index(key) + 1]

    def prints(args):
        return [args[i + 1] for i, a in enumerate(args) if a == "--print"]

    # ---- login status from synthetic cookie fixtures only (separate app root) ----
    capp = fixture / "cookie-app"
    (capp / "bin").mkdir(parents=True)
    cenv = dict(env, SG_TEST_APP=str(capp))
    future, past = int(time.time()) + 86400 * 30, int(time.time()) - 86400

    def row(exp, name="SYNTH_NAME_A", domain=".youtube.com"):
        return "\t".join([domain, "TRUE", "/", "TRUE", str(exp), name, "synthetic-cookie-value"])
    yt_cases = [(None, "guest"), ([row(future)], "signed-in"), ([row(0)], "signed-in"), ([row(past), row(past, "SYNTH_NAME_B")], "expired"),
                ([row(past), row(future, "SYNTH_NAME_B")], "signed-in"), ([row(future, domain=".evil.invalid")], "guest"),
                ([row(future), row(future, domain=".evil.invalid")], "guest"),
                ([row(future), row(future, "SYNTH_NAME_B").replace("\tTRUE\t/", "\tINVALID\t/")], "guest"),
                (["not a cookie row"], "guest"), ([], "guest"), (["# comment only"], "guest")]
    x_cases = [(None, "guest"), ([row(future, "auth_token", "x.com"), row(future, "ct0", "x.com")], "signed-in"),
               ([row(future, "auth_token", "x.com")], "guest"), ([row(past, "auth_token", "x.com"), row(past, "ct0", "x.com")], "expired"),
               ([row(future, "auth_token", "x.com"), row(past, "ct0", "x.com")], "expired"),
               ([row(future, "auth_token", "x.com"), row(future, "ct0", "twitter.com")], "guest"),
               ([row(future, "auth_token", "x.com"), row(future, "ct0", "x.com"), row(future, domain=".evil.invalid")], "guest"),
               ([row(future, "auth_token", ".youtube.com")], "guest")]
    for name, cases, key in (("cookies_youtube.txt", yt_cases, "youtube"), ("cookies_twitter.txt", x_cases, "x")):
        for rows, expected in cases:
            path = capp / "bin" / name
            if rows is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text("# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n", encoding="utf-8")
            ping = call(exe, {"action": "ping"}, cenv)
            text = json.dumps(ping, ensure_ascii=False)
            check(ping["login"][key] == expected, "login " + key + " " + expected + " " + repr(rows and len(rows)))
            check(set(ping["login"]) == {"youtube", "x"} and json.dumps(ping["login"]).count(":") == 2, "login only has two enum values")
            check("synthetic-cookie-value" not in text and "SYNTH_NAME" not in text and "auth_token" not in text
                  and "cookies_" not in text, "ping never leaks cookie names/values/paths")
        (capp / "bin" / name).unlink(missing_ok=True)

    # ---- strict validation / incompatible combinations (no job, no engine) ----
    xurl = "https://x.com/test/status/123"
    rejected = [
        {"sponsorblock": "maybe"}, {"sponsorblock": True}, {"sponsorblock": None}, {"sponsorblock": "SPONSOR"},
        {"sponsorblock": "sponsor", "url": xurl}, {"sponsorblock": "all", "url": xurl, "action": "x-download"},
        {"section": "1:30"}, {"section": "2:00-1:00"}, {"section": "5-5"}, {"section": "0-86401"}, {"section": "1:60-2:00"},
        {"section": "a-b"}, {"section": 5}, {"section": None}, {"section": "1-2;calc"}, {"section": "1-" + "1" * 40},
        {"section": "-1-5"}, {"section": "inf-5"}, {"section": "1:2:3:4-5"}, {"section": "1.2345-5"}, {"section": "1-2\n"},
        {"playlist": "true"}, {"playlist": 1}, {"playlist": None}, {"playlist": True}, {"playlist": True, "url": xurl},
        {"url": PLAYLIST}, {"url": PLAYLIST, "playlist": False}, {"items": "1-10"}, {"items": 5},
        {"playlist": True, "url": PLAYLIST, "items": "abc"}, {"playlist": True, "url": PLAYLIST, "items": "1-10,,3"},
        {"playlist": True, "url": PLAYLIST, "items": "1:3"}, {"playlist": True, "url": PLAYLIST, "items": ",".join(["1"] * 21)},
        {"playlist": True, "url": PLAYLIST, "items": "123456"}, {"playlist": True, "url": PLAYLIST, "items": 7},
        {"playlist": True, "url": "https://www.youtube.com/playlist?list=bad%20id"},
        {"live": "yes"}, {"live": True}, {"live": "from-start", "url": xurl}, {"live": "from-start", "sponsorblock": "sponsor"},
        {"live": "from-start", "playlist": True, "url": PLAYLIST}, {"live": "from-start", "section": "1-2"},
        {"liveMaxMinutes": 0}, {"liveMaxMinutes": 721}, {"liveMaxMinutes": "60"}, {"liveMaxMinutes": 60.5}, {"liveMaxMinutes": True},
        {"liveMaxMinutes": None},
    ]
    before = job_count()
    for changed in rejected:
        reply = call(exe, {**base, **changed}, env)
        check(reply.get("ok") is False and reply.get("error") and any("\u4e00" <= c <= "\u9fff" for c in reply["error"]),
              "invalid new option rejected with Chinese error: " + repr(changed) + " -> " + repr(reply))
    check(job_count() == before, "rejected new options create no job")
    reply = call(exe, {**base, "media": "audio-original", "sponsorblock": "sponsor"}, env)
    check(reply.get("error") == "原音轨不重新编码，无法移除片段；请改选 MP3 / M4A 或关闭跳过片段。", "audio-original + sponsorblock exact error")
    for combo in ({"section": "1:30-2:45"}, {"live": "from-start"}):
        reply = call(exe, {**base, "media": "audio-original", **combo}, env)
        check(reply.get("ok") is False and "原音轨" in reply.get("error", ""), "audio-original incompatible " + repr(combo))
    check("片段" in call(exe, {**base, "playlist": True, "url": PLAYLIST, "section": "1-2"}, env).get("error", ""), "playlist + section error")
    check("直播" in call(exe, {**base, "live": "from-start", "url": xurl}, env).get("error", ""), "live on X error")

    # ---- SponsorBlock ----
    for changed in ({"embedThumbnail": "yes"}, {"splitChapters": 1}, {"downloadArchive": None},
                    {"limitRate": "1M --exec bad"}, {"concurrentFragments": 4}, {"concurrentFragments": "99"},
                    {"splitChapters": True, "section": "1-2"}, {"splitChapters": True, "live": "from-start"},
                    {"downloadArchive": True, "live": "from-start"}, {"embedThumbnail": True, "media": "audio-original"}):
        check(call(exe, {**base, **changed}, env).get("ok") is False, "invalid practical option rejected before download: " + repr(changed))
    completed, _ = wait(exe, accept(811, embedThumbnail=True, limitRate="1M", concurrentFragments="4"), env)
    argv = invocation()
    check(completed["state"] == "completed" and "--embed-thumbnail" in argv and arg(argv, "--limit-rate") == "1M"
          and arg(argv, "--concurrent-fragments") == "4", "cover and transfer options reach downloader")
    completed, _ = wait(exe, accept(812, splitChapters=True), env)
    check(completed["state"] == "completed" and completed["fileCount"] == 3
          and len(completed["files"]) == 3 and sum(".chapters" in p for p in completed["files"]) == 2, "chapter outputs recorded alongside full media")
    check(call(exe, {"action": "open-job-file", "jobId": completed["id"], "fileIndex": 1}, env).get("ok") is True, "open recorded chapter by index")
    for changed in ({"fileIndex": -1}, {"fileIndex": "0"}, {"fileIndex": 30}, {"fileIndex": 0, "path": "C:\\Windows\\cmd.exe"}):
        check(call(exe, {"action": "open-job-file", "jobId": completed["id"], **changed}, env).get("ok") is False, "file open rejects invalid index or arbitrary path")
    chapter = Path(completed["files"][1]); chapter.unlink()
    check(call(exe, {"action": "open-job-file", "jobId": completed["id"], "fileIndex": 1}, env).get("ok") is False, "deleted chapter does not open a different file")
    archived, _ = wait(exe, accept(813, downloadArchive=True), env)
    check(archived["state"] == "completed" and archived["files"], "first archived download saves media")
    repeated, _ = wait(exe, accept(813, downloadArchive=True), env)
    check(repeated["state"] == "completed" and not repeated["files"] and "视频已存在，已跳过重复下载。" in repeated["warnings"], "repeat is visibly skipped instead of failed")
    changed, _ = wait(exe, accept(813, downloadArchive=True, quality="720"), env)
    check(changed["state"] == "completed" and changed["files"], "different output quality has its own archive")
    env["SG_TEST_MODE"] = "download-fail"
    failed, _ = wait(exe, accept(814, downloadArchive=True), env)
    env.pop("SG_TEST_MODE")
    recovered, _ = wait(exe, accept(814, downloadArchive=True), env)
    check(failed["state"] == "failed" and recovered["state"] == "completed" and recovered["files"], "failure never poisons persistent archive")
    check(not list((native / "jobs").glob("*.archive")), "per-job archive scratch files cleaned")

    sb = accept(sponsorblock="sponsor")
    check(accept(sponsorblock="sponsor").get("jobId") == sb.get("jobId"), "sponsorblock dedup same")
    sb_all = accept(sponsorblock="all")
    check(sb_all.get("jobId") != sb.get("jobId"), "sponsorblock part of dedup key")
    state, _ = wait(exe, sb, env)
    wait(exe, sb_all, env)
    check(state["state"] == "completed" and state["sponsorblock"] == "sponsor", "sponsorblock stored/returned")
    args = invocation()
    check(arg(args, "--sponsorblock-remove") == SB_ALL and "--sponsorblock-mark" not in args, "sponsorblock all categories, no mark")
    state, _ = wait(exe, accept(media="audio-mp3", sponsorblock="sponsor"), env)
    args = invocation()
    check(state["state"] == "completed" and arg(args, "--sponsorblock-remove") == "sponsor" and "-x" in args, "sponsorblock audio mode")
    state, _ = wait(exe, accept(media="audio-m4a", audioQuality="192", sponsorblock="all"), env)
    check(state["state"] == "completed" and arg(invocation(), "--sponsorblock-remove") == SB_ALL, "sponsorblock explicit-quality audio")
    wait(exe, accept(sponsorblock="off", quality="1080"), env)
    check("--sponsorblock-remove" not in invocation(), "sponsorblock off adds nothing")

    # ---- sections ----
    sec = accept(section="1:30-2:45")
    check(accept(section="90-165").get("jobId") == sec.get("jobId"), "section dedup uses normalized range")
    other = accept(section="90-166")
    check(other.get("jobId") != sec.get("jobId"), "section part of dedup key")
    state, _ = wait(exe, sec, env)
    wait(exe, other, env)
    stored = json.loads((jobs / (sec["jobId"] + ".json")).read_text(encoding="utf-8"))
    check(state["state"] == "completed" and state["section"] == "1:30-2:45" and stored["section"] == "1:30-2:45", "section stored/returned")
    for section, normalized, suffix, extra in (("1:30-2:45", "*90-165", "[90-165]", {"quality": "480"}),
                                               ("0:01:05.5-inf", "*65.5-inf", "[65.5-inf]", {"url": "https://x.com/test/status/4501"}),
                                               ("00:00:00-24:00:00", "*0-86400", "[0-86400]", {}),
                                               ("7.25-8.125", "*7.25-8.125", "[7.25-8.125]", {"quality": "720"})):
        state, _ = wait(exe, accept(section=section, **extra), env)
        args = invocation()
        check(state["state"] == "completed" and arg(args, "--download-sections") == normalized and "--force-keyframes-at-cuts" in args,
              "section args " + section)
        check(arg(args, "--output") == "%(title).150B [%(id)s] " + suffix + ".%(ext)s" and "--no-playlist" in args, "section filename suffix " + section)
    state, _ = wait(exe, accept(media="audio-mp3", section="10-20"), env)
    args = invocation()
    check(state["state"] == "completed" and arg(args, "--download-sections") == "*10-20" and "--force-keyframes-at-cuts" not in args,
          "audio section has no forced keyframes")
    wait(exe, accept(section="", quality="480"), env)
    args = invocation()
    check("--download-sections" not in args and arg(args, "--output") == "%(title).150B [%(id)s].%(ext)s", "empty section is whole video")
    section_minutes_test(exe, env, app, base)

    # ---- playlist direct download ----
    pl = accept(url=PLAYLIST, playlist=True)
    check(pl.get("ok") is True, "playlist accepted: " + repr(pl))
    state, _ = wait(exe, pl, env)
    args = invocation()
    check(state["state"] == "completed" and state["playlist"] is True and state["items"] == "" and state["url"] == PLAYLIST, "playlist completes")
    check("--yes-playlist" in args and "--no-playlist" not in args and arg(args, "--playlist-items") == "1-200"
          and arg(args, "--max-downloads") == "200" and "--ignore-errors" in args, "playlist caps/flags")
    check(arg(args, "--output") == "%(playlist_title).80B/%(playlist_index)03d - %(title).120B [%(id)s].%(ext)s", "playlist template")
    check(arg(args, "--progress-template") == "download:SGPROGRESS:%(info.playlist_index)s/%(info.n_entries)s:%(progress._percent_str)s",
          "playlist progress template")
    check("playlist:SGPLTITLEJ:%(title)j" in prints(args) and args[-1] == PLAYLIST, "playlist title print and URL")
    check("before_dl:SGIDJ:%(id)j" in prints(args), "playlist item IDs requested before downloads for owned cancel cleanup")
    check(state["title"] == "合成列表" and state["fileCount"] == 3 and len(state["files"]) == 3 and state["warnings"] == []
          and all(Path(f).parent == downloads / "合成列表" for f in state["files"]), "playlist files in subfolder")
    state, _ = wait(exe, accept(url="https://www.youtube.com/watch?v=abcdefghijk&list=PLsynthetic0002&index=3", playlist=True, items="1-2,5,7-"), env)
    args = invocation()
    check(state["state"] == "completed" and state["url"] == "https://www.youtube.com/playlist?list=PLsynthetic0002"
          and args[-1] == state["url"] and arg(args, "--playlist-items") == "1-2,5,7-" and state["items"] == "1-2,5,7-", "watch+list playlist with items")
    env["SG_TEST_ITEMS"] = "23"
    state, _ = wait(exe, accept(url=PLAYLIST, playlist=True, items="1-23"), env)
    env.pop("SG_TEST_ITEMS")
    check(state["state"] == "completed" and len(state["files"]) == 20 and state["fileCount"] == 23, "playlist files cap 20 with fileCount")
    env["SG_TEST_MODE"] = "playlist-partial"
    state, _ = wait(exe, accept(url=PLAYLIST, playlist=True, items="1-3"), env)
    check(state["state"] == "completed" and state["fileCount"] == 2 and state["warnings"] == ["部分条目下载失败。"]
          and "synthetic-private-value" not in json.dumps(state), "playlist partial failure warning")
    env["SG_TEST_MODE"] = "playlist-allfail"
    state, _ = wait(exe, accept(url=PLAYLIST, playlist=True, items="2-3"), env)
    check(state["state"] == "failed" and state["files"] == [] and state["fileCount"] == 0, "playlist with no files fails")
    env["SG_TEST_MODE"] = "playlist-progress"
    env["SG_TEST_ITEMS"] = "4"
    progress_job = accept(url=PLAYLIST, playlist=True, items="1-4")
    seen = set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = status(progress_job["jobId"])
        seen.add(job["percent"])
        if job["state"] not in ("queued", "downloading"):
            break
        time.sleep(.05)
    env.pop("SG_TEST_MODE")
    env.pop("SG_TEST_ITEMS")
    check(37.5 in seen, "combined playlist progress ((2-1)+0.5)/4: " + repr(sorted(seen)))
    wait(exe, progress_job, env)
    env["SG_TEST_MODE"] = "playlist-legacy-progress"
    legacy_progress_job = accept(url=PLAYLIST, playlist=True, items="1-3")
    env.pop("SG_TEST_MODE")
    seen = set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = status(legacy_progress_job["jobId"])
        seen.add(job["percent"])
        if job["state"] not in ("queued", "downloading"):
            break
        time.sleep(.05)
    check({12.5, 42.0}.issubset(seen), "playlist accepts old padded markers on stdout and stderr: " + repr(sorted(seen)))
    wait(exe, legacy_progress_job, env)
    state, _ = wait(exe, accept(url=PLAYLIST, playlist=True, media="audio-mp3", audioQuality="192", sponsorblock="sponsor", items="1-3"), env)
    check(state["state"] == "completed" and state["fileCount"] == 3 and all(Path(f).parent == downloads / "合成列表"
          and f.endswith("[192k].mp3") for f in state["files"]), "playlist explicit audio published into playlist subfolder")
    state, _ = wait(exe, accept(url=PLAYLIST, playlist=True, subtitles="manual", items="1"), env)
    check(state["state"] == "completed" and "--write-subs" in invocation(), "playlist allows subtitles")
    playlist_audio_title_test(exe, env, downloads)
    postprocess_cancel_test(exe, env, app, native, downloads)

    # ---- cancel ----
    for bad in ({"action": "cancel-job", "jobId": uuid.uuid4().hex, "path": "x"}, {"action": "cancel-job", "jobId": "../x"},
                {"action": "cancel-job"}, {"action": "cancel-job", "jobId": 5}):
        check(call(exe, bad, env).get("ok") is False, "cancel-job strict request " + repr(bad))
    missing = call(exe, {"action": "cancel-job", "jobId": uuid.uuid4().hex}, env)
    check(missing == {"ok": False, "error": "找不到此下载任务。"}, "cancel unknown job")
    done = accept(quality="720", metadata=True)
    done_state, _ = wait(exe, done, env)
    reply = call(exe, {"action": "cancel-job", "jobId": done["jobId"]}, env)
    check(reply == {"ok": True, "jobId": done["jobId"], "state": "completed"} and not (jobs / (done["jobId"] + ".cancel")).exists()
          and status(done["jobId"]) == done_state, "cancel completed job is idempotent no-op")

    def run_cancel(label, request, expect_files=0, mode="slow"):
        for name in ("engine.pid", "child.pid"):
            (app / name).unlink(missing_ok=True)
        env["SG_TEST_MODE"] = mode
        started = accept(**request)
        env.pop("SG_TEST_MODE")
        check(started.get("ok") is True, "cancel fixture accepted " + label)
        wait_for(lambda: (app / "engine.pid").exists() and (app / "child.pid").exists(), "engine/child started " + label)
        engine_pid = int((app / "engine.pid").read_text())
        child_pid = int((app / "child.pid").read_text())
        wait_for(lambda: status(started["jobId"])["percent"] > 0, "progress " + label)
        if expect_files:
            wait_for(lambda: status(started["jobId"])["fileCount"] == expect_files, "completed outputs before cancel " + label)
        args = invocation()
        check(alive(engine_pid) and alive(child_pid), "mock engine and child running " + label)
        interim = status(started["jobId"])
        check(interim["state"] == "downloading", "downloading before cancel " + label)
        t0 = time.monotonic()
        reply = call(exe, {"action": "cancel-job", "jobId": started["jobId"]}, env)
        check(reply == {"ok": True, "jobId": started["jobId"], "state": "canceling"}, "cancel returns canceling " + label)
        final, _ = wait(exe, started, env)
        elapsed = time.monotonic() - t0
        check(final["state"] == "canceled" and final["error"] == "已取消下载。" and final["percent"] == interim["percent"],
              "canceled terminal state/percent unchanged " + label + " " + repr(final))
        check(elapsed < 6, "cancel observed promptly " + label + " %.2fs" % elapsed)
        wait_for(lambda: not alive(engine_pid) and not alive(child_pid), "engine tree killed " + label, 10)
        check(len(final["files"]) == expect_files and final["fileCount"] == expect_files, "only completed outputs kept " + label)
        wait_for(lambda: not (jobs / (started["jobId"] + ".cancel")).exists(), "marker removed " + label)
        check(not list(jobs.glob(started["jobId"] + ".audio-*")) and not (jobs / (started["jobId"] + ".cookies.txt")).exists()
              and not (jobs / (started["jobId"] + ".temp")).exists(), "staging/private temp/cookie temp removed " + label)
        if "--cookies" in args:
            check(not Path(arg(args, "--cookies")).exists(), "cookie temp from invocation removed " + label)
        again = call(exe, {"action": "cancel-job", "jobId": started["jobId"]}, env)
        check(again == {"ok": True, "jobId": started["jobId"], "state": "canceled"} and not (jobs / (started["jobId"] + ".cancel")).exists(),
              "repeat cancel idempotent " + label)
        print("CANCEL", label, "terminated engine", engine_pid, "and child", child_pid, "in %.2fs" % elapsed)
        return started, final, args

    keep = {downloads / "旧 [abcdefghijk].f137.mp4.part": b"preexisting partial of same id",
            downloads / "已完成 [abcdefghijk].mp4": b"preexisting completed media",
            downloads / "旧 [abcdefghijk].temp.mp4": b"preexisting temp"}
    for path, content in keep.items():
        path.write_bytes(content)
    time.sleep(.05)
    cookie_fixture = app / "bin/cookies_youtube.txt"
    cookie_fixture.write_text("# Netscape HTTP Cookie File\n" + row(future) + "\n", encoding="utf-8")
    try:
        started, final, args = run_cancel("video", {"quality": "1080", "sponsorblock": "sponsor"})
        check("--cookies" in args, "cancel fixture used per-job cookie temp file")
    finally:
        cookie_fixture.unlink()
    for name in ("合成 [abcdefghijk].f137.mp4.part", "合成 [abcdefghijk].f137.mp4.ytdl", "合成 [abcdefghijk].temp.mp4"):
        check(not (downloads / name).exists(), "owned temp removed " + name)
    check((downloads / "并发 [zzzzzzzzzzz].mp4.part").read_bytes() == b"concurrent unrelated job", "unrelated concurrent partial kept")
    check(all(path.read_bytes() == content for path, content in keep.items()), "pre-existing files (even same id) survive cancel")
    same_id_temp_ownership_test(exe, env, app, downloads)
    started, final, args = run_cancel("staged audio", {"media": "audio-mp3", "audioQuality": "256"})
    check(Path(arg(args, "--paths")).is_relative_to(jobs) and not Path(arg(args, "--paths")).exists(), "cancel removes audio staging")
    started, final, args = run_cancel("playlist mid-list", {"url": PLAYLIST, "playlist": True, "items": "1-3"}, 1, "playlist-slow")
    check(Path(final["files"][0]).name == "001 - 条目 1 [plitem00001].mp4" and Path(final["files"][0]).exists(), "finished playlist item kept")
    check(not (downloads / "合成列表" / "002 - 条目 2 [plitem00002].mp4.part").exists(), "unfinished playlist item partial removed")
    started, final, args = run_cancel("playlist staged audio", {"url": PLAYLIST, "playlist": True, "items": "1-2", "media": "audio-m4a",
                                                                "audioQuality": "128"}, 1, "playlist-slow")
    check(final["files"][0].endswith("[128k].m4a") and Path(final["files"][0]).parent == downloads / "合成列表",
          "finished staged playlist item published on cancel")

    # Cancel while queued: marker present before the worker starts the engine.
    stamp = (app / "invocation.json").read_bytes()
    queued_id = uuid.uuid4().hex
    queued = {"id": queued_id, "url": base["url"], "quality": "best", "downloadDir": str(downloads), "state": "queued", "percent": 0.0,
              "pid": 0, "cookieStatus": "guest", "media": "video", "files": [], "created": ticks(), "updated": ticks(), "error": ""}
    (jobs / (queued_id + ".json")).write_text(json.dumps(queued), encoding="utf-8")
    (jobs / (queued_id + ".cancel")).write_bytes(b"")
    result = subprocess.run([str(exe), "--worker", queued_id], env=env, capture_output=True, timeout=20)
    check(result.returncode == 0, "queued worker exits")
    qs = status(queued_id)
    check(qs["state"] == "canceled" and qs["error"] == "已取消下载。" and qs["percent"] == 0 and qs["files"] == [], "queued cancel finalizes")
    check((app / "invocation.json").read_bytes() == stamp and not (jobs / (queued_id + ".cancel")).exists(), "queued cancel never starts engine")
    queued_cancel_race_test(exe, env, app, native, downloads, base)
    # Dead worker with marker -> canceled (not the generic stale failure).
    dead = subprocess.Popen(["cmd.exe", "/d", "/c", "exit 0"])
    dead.wait()
    for pid, updated in ((dead.pid, ticks()), (0, ticks(-120))):
        dead_id = uuid.uuid4().hex
        record = {**queued, "id": dead_id, "state": "downloading", "pid": pid, "percent": 12.5, "updated": updated}
        (jobs / (dead_id + ".json")).write_text(json.dumps(record), encoding="utf-8")
        (jobs / (dead_id + ".cancel")).write_bytes(b"")
        (jobs / (dead_id + ".cookies.txt")).write_text("# synthetic\n", encoding="utf-8")
        ds = status(dead_id)
        check(ds["state"] == "canceled" and ds["error"] == "已取消下载。" and ds["percent"] == 12.5, "dead worker + marker finalizes canceled")
        check(not (jobs / (dead_id + ".cancel")).exists() and not (jobs / (dead_id + ".cookies.txt")).exists(), "dead cancel removes marker/cookie")
    # Dedup must ignore canceled jobs and jobs with a pending cancel marker.
    seeded_canceled = {**queued, "id": uuid.uuid4().hex, "state": "canceled", "updated": ticks()}
    (jobs / (seeded_canceled["id"] + ".json")).write_text(json.dumps(seeded_canceled), encoding="utf-8")
    pending = {**queued, "id": uuid.uuid4().hex, "state": "queued", "updated": ticks()}
    (jobs / (pending["id"] + ".json")).write_text(json.dumps(pending), encoding="utf-8")
    (jobs / (pending["id"] + ".cancel")).write_bytes(b"")
    fresh = accept()
    check(fresh.get("jobId") not in (seeded_canceled["id"], pending["id"], queued_id), "dedup ignores canceled and canceling jobs")
    check(wait(exe, fresh, env)[0]["state"] == "completed", "new job after canceled completes")
    for leftover in (pending["id"] + ".json", pending["id"] + ".cancel"):
        (jobs / leftover).unlink()

    # ---- live from start with duration cap (BRIDGE_TEST scales the cap to seconds) ----
    env["SG_TEST_LIVE_SECONDS"] = "2"
    for name in ("engine.pid", "child.pid"):
        (app / name).unlink(missing_ok=True)
    live = accept(url="https://www.youtube.com/live/livevideo01", live="from-start", liveMaxMinutes=60)
    check(live.get("ok") is True, "live accepted " + repr(live))
    wait_for(lambda: (app / "child.pid").exists(), "live engine started")
    engine_pid, child_pid = int((app / "engine.pid").read_text()), int((app / "child.pid").read_text())
    state, _ = wait(exe, live, env)
    args = invocation()
    check("--live-from-start" in args and arg(args, "--wait-for-video") == "15" and "--no-playlist" in args, "live args")
    check(state["state"] == "completed" and state["live"] == "from-start" and state["warnings"] == ["已达到直播录制时长上限。"]
          and [Path(f).name for f in state["files"]] == ["直播 [livevideo01].mp4"] and state["percent"] == 100, "live cap keeps recording " + repr(state))
    wait_for(lambda: not alive(engine_pid) and not alive(child_pid), "live tree stopped")
    check(not (downloads / "直播 [livevideo01].mp4.ytdl").exists() and not (downloads / "直播 [livevideo01].mp4.part-Frag7.part").exists()
          and not (downloads / "直播 [livevideo01].mp4.part").exists(), "live temp fragments cleaned")
    stored = json.loads((jobs / (live["jobId"] + ".json")).read_text(encoding="utf-8"))
    check(stored["liveMaxMinutes"] == 60 and stored["live"] == "from-start", "live minutes stored")
    env["SG_TEST_MODE"] = "live-empty"
    state, _ = wait(exe, accept(url="https://www.youtube.com/watch?v=livevideo02", live="from-start"), env)
    env.pop("SG_TEST_MODE")
    check(state["state"] == "failed" and state["error"] == "直播录制已达到时长上限，未生成文件。", "live cap without file fails clearly")
    check(json.loads((jobs / (state["id"] + ".json")).read_text(encoding="utf-8"))["liveMaxMinutes"] == 240, "live default 240 minutes")
    state, _ = wait(exe, accept(url="https://www.youtube.com/watch?v=livevideo03", live="from-start", media="audio-mp3", audioQuality="192"), env)
    check(state["state"] == "completed" and [Path(f).name for f in state["files"]] == ["直播 [livevideo03] [192k].mp3"],
          "live staged audio cap encodes recording " + repr(state))
    env.pop("SG_TEST_LIVE_SECONDS")
    live_part_ownership_test(exe, env, app, downloads)
    wait(exe, accept(live="off", liveMaxMinutes=720, quality="720", metadata=True), env)
    check("--live-from-start" not in invocation(), "live off adds nothing")

    # ---- pick-folder initialDir (validation only; never opens UI) ----
    for bad in ({"action": "pick-folder", "path": "C:\\"}, {"action": "pick-folder", "initialDir": "C:\\", "extra": 1},
                {"action": "test-pick-folder-initial", "initialDir": "C:\\", "x": 1}):
        reply = call(exe, bad, env)
        check(reply.get("ok") is False and "initialDir" in reply.get("error", ""), "pick-folder extra keys rejected " + repr(bad))
    windows = os.environ["WINDIR"]
    junction = fixture / "pick-junction"
    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(other_dir)], capture_output=True, timeout=10, check=True)
    try:
        for value, expected in ((str(downloads), str(downloads)), (windows, windows), (str(downloads) + "\\", str(downloads)),
                                (str(downloads / ".." / downloads.name), str(downloads)),
                                ("relative", ""), ("C:", ""), ("\\\\server\\share", ""), ("\\\\?\\C:\\Windows", ""), (str(downloads / "missing"), ""),
                                (str(app / "outside.mp4"), ""), (str(junction), ""), (str(downloads) + "\t", ""), ("", ""),
                                (5, ""), (None, ""), ([], ""), ({"p": 1}, ""), (True, ""), ("C:\\" + "a" * 300, "")):
            reply = call(exe, {"action": "test-pick-folder-initial", "initialDir": value}, env)
            check(reply == {"ok": True, "initialDir": expected}, "initialDir silent validation " + repr(value) + " -> " + repr(reply))
        check(call(exe, {"action": "test-pick-folder-initial"}, env) == {"ok": True, "initialDir": ""}, "missing initialDir")
    finally:
        os.rmdir(junction)
    print("PASS feature tests")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-output", type=Path)
    options = ap.parse_args()
    with FixtureDirectory(prefix="native_veylo_run_", dir=ROOT / "tests") as temp:
        fixture = Path(temp)
        native = fixture / "native"
        native.mkdir()
        app = fixture / "FluentYTDL"
        (app / "bin/yt-dlp").mkdir(parents=True)
        (app / "bin/ffmpeg").mkdir()
        downloads = fixture / "下载, local"
        downloads.mkdir()
        other_dir = fixture / "other"
        other_dir.mkdir()
        config = {"download_dir": str(downloads), "proxy_mode": "off", "proxy_url": ""}
        (app / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (app / "outside.mp4").write_bytes(b"not in selected directory")
        mock = fixture / "Mock.cs"
        mock.write_text(MOCK, encoding="utf-8")
        compile_cs(mock, app / "bin/yt-dlp/yt-dlp.exe")
        encoder = fixture / "MockFfmpeg.cs"
        encoder.write_text(MOCK_FFMPEG, encoding="utf-8")
        compile_cs(encoder, app / "bin/ffmpeg/ffmpeg.exe")
        exe = native / "ShiguangBridge.exe"
        release = fixture / "release/ShiguangBridge.exe"
        release.parent.mkdir()
        compile_cs(ROOT / "native/ShiguangBridge.cs", exe, True)
        compile_cs(ROOT / "native/ShiguangBridge.cs", release)
        paths = release.parent / "fluent_paths.json"
        paths.write_text(json.dumps({"fluent_root": str(app)}), encoding="utf-8")
        release_runner = release.parent / "IsolatedRelease.exe"
        compile_cs(ROOT / "tests/MockShiguangRelease.cs", release_runner)
        env = dict(os.environ, SG_TEST_APP=str(app))
        release_env = dict(env, SG_TEST_RELEASE=str(release), SG_TEST_RELEASE_HOME=str(release.parent))

        def release_info(mode):
            result = subprocess.run([str(release_runner)], env=dict(release_env, SG_TEST_RELEASE_MODE=mode, SG_TEST_LIVE_SECONDS="1"),
                                    capture_output=True, timeout=15)
            check(result.returncode == 0 and not result.stderr, "isolated release " + mode)
            return json.loads(result.stdout)

        ping = call(exe, {"action": "ping"}, env)
        check(ping["version"] == "1.7.0" and ping["engineAvailable"] and ping["appRoot"] == str(app), "test ping/version")
        check(ping.get("audioQualityOptions") == ["auto", "320", "256", "192", "128"], "explicit audio capability advertised")
        check(ping.get("mediaOptions") == ["video", "audio-original", "audio-mp3", "audio-m4a"], "original audio capability advertised")
        prod_ping = call(release_runner, {"action": "ping"}, release_env)
        check(prod_ping["version"] == "1.7.0" and prod_ping["appRoot"] == str(app) and prod_ping["engineAvailable"], "isolated release ping/version/root/engine")
        check(prod_ping.get("mediaOptions") == ping["mediaOptions"], "release original capability matches test host")
        check(ping.get("features") == FEATURES and prod_ping.get("features") == FEATURES, "exact ordered feature list (test + release)")
        check(ping.get("login") == {"youtube": "guest", "x": "guest"} and prod_ping.get("login") == {"youtube": "guest", "x": "guest"}, "login status shape")
        check(call(release_runner, {"action": "test-pick-folder-initial", "initialDir": "C:\\"}, release_env).get("ok") is False, "test-only action absent from release")
        info = release_info("metadata")
        check(info["version"] == "1.7.0.0" and info["fileVersion"] == "1.7.0.0", "release assembly/file version")
        check(info["dialogMethods"] == ["Show", "SetFileTypes", "SetFileTypeIndex", "GetFileTypeIndex", "Advise", "Unadvise", "SetOptions", "GetOptions",
                                         "SetDefaultFolder", "SetFolder", "GetFolder", "GetCurrentSelection", "SetFileName", "GetFileName", "SetTitle",
                                         "SetOkButtonLabel", "SetFileNameLabel", "GetResult", "AddPlace", "SetDefaultExtension", "Close", "SetClientGuid", "ClearClientData", "SetFilter"]
              and info["shellItemInterface"], "release COM vtable order and marshalled IShellItem")
        check(info["liveLimitSeconds"] == 3600, "release ignores test-only live duration override")
        check(release_info("resolve") == str(app), "bridge-owned explicit fluent_root takes priority")
        paths.unlink()
        fallback = release_info("resolve")
        paths.write_text(json.dumps({"fluent_root": "\\\\server\\share"}), encoding="utf-8")
        check(release_info("resolve") == fallback, "unsafe root config falls back to bounded discovery without dispatch or Cookie reads")
        paths.write_text(json.dumps({"fluent_root": str(app)}), encoding="utf-8")
        print("PASS release metadata/ping isolated from real config and Cookie files")
        check(exchange(exe, [{"action": "ping"}], env, None)[0]["ok"], "legacy no-origin call")
        wrong = subprocess.run([str(exe), "chrome-extension://" + "b" * 32 + "/"], input=frame({"action": "ping"}), env=env, capture_output=True, timeout=10)
        check(wrong.returncode != 0 and not wrong.stdout, "origin restriction")
        for wire in (b"\x04\x00", struct.pack("<I", 0), struct.pack("<I", 65537), struct.pack("<I", 20) + b"{}"):
            result = subprocess.run([str(exe)], input=wire, env=env, capture_output=True, timeout=10)
            check(result.returncode != 0 and not result.stdout, "malformed input frame")

        base = {"action": "yt-download", "url": "https://www.youtube.com/watch?v=abcdefghijk"}
        engine = app / "bin/yt-dlp/yt-dlp.exe"
        unavailable = engine.with_suffix(".unavailable")
        engine.rename(unavailable)
        try:
            for media in ("video", "audio-original", "audio-mp3", "audio-m4a"):
                for value in (None, 320, {}, [], True, "", "320k", "AUTO", "192 --exec bad"):
                    rejected = call(exe, {**base, "media": media, "audioQuality": value}, env)
                    check(rejected.get("ok") is False and "audioQuality" in rejected.get("error", ""),
                          "invalid present audioQuality rejected before tool/config work")
            check(not list((native / "jobs").iterdir()) and not (app / "invocation.json").exists(),
                  "invalid audioQuality creates no job or tool invocation")
        finally:
            unavailable.rename(engine)
        fixture_config = app / "config.json"
        held_config = app / "config.held"
        fixture_config.rename(held_config)
        try:
            for value in (None, "auto", "320", "256", "192", "128"):
                request = {**base, "media": "audio-original"}
                if value is not None:
                    request["audioQuality"] = value
                rejected = call(exe, request, env)
                check(rejected.get("ok") is False and "ffprobe.exe" in rejected.get("error", ""),
                      "original missing adjacent probe rejects before config or network work")
            check(not list((native / "jobs").iterdir()) and not (app / "invocation.json").exists(), "missing probe creates no job or downloader work")
        finally:
            held_config.rename(fixture_config)
        invalid = [
            {"media": "mp3"}, {"media": "video --exec bad"}, {"media": 1}, {"media": None},
            {"subtitles": "all"}, {"subtitles": True}, {"subLangs": "en,--exec"}, {"subLangs": ["en"]},
            {"metadata": "true"}, {"metadata": 1}, {"quality": "999"}, {"part": "all"},
            {"media": "audio-mp3", "subtitles": "manual"},
            {"url": "https://x.com/test/status/123", "subtitles": "auto"},
            {"url": "http://x.com/test/status/123"},
            {"url": "https://x.com.evil.invalid/test/status/123"},
            {"url": "https://user:password@x.com/test/status/123"},
            {"url": "https://x.com:8443/test/status/123"},
            {"url": "https://www.youtube.com/playlist?list=anything"},
        ]
        for changed in invalid:
            check(call(exe, {**base, **changed}, env).get("ok") is False, "invalid URL/option rejected")

        def accept(number=None, **kw):
            msg = dict(base)
            if number is not None:
                msg["url"] = "https://x.com/test/status/" + str(number)
            msg.update(kw)
            return call(exe, msg, env)

        def invocation():
            # The asynchronous mock truncates and rewrites this observation file.
            # Wait for a complete JSON document instead of treating an in-flight write as a host failure.
            for attempt in range(50):
                try:
                    return json.loads((app / "invocation.json").read_text(encoding="utf-8"))
                except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                    if attempt == 49:
                        raise
                    time.sleep(.01)

        accepted = accept(101, action="x-download", quality="720", audioQuality="320")
        duplicate = accept(101, quality="720", audioQuality="auto")
        check(duplicate.get("jobId") == accepted.get("jobId"), "x-download compatibility alias and duplicate")
        state, progress = wait(exe, accepted, env)
        check(state["state"] == "completed" and state["percent"] == 100 and progress, "default video completion/progress")
        args = invocation()
        check(args[args.index("--format") + 1] == "bv*[height<=720]+ba/b[height<=720]", "legacy video quality selector")
        check("--remux-video" in args and "--merge-output-format" in args and "-x" not in args, "legacy video container options")
        check("--write-subs" not in args and "--embed-metadata" not in args and args[args.index("--proxy") + 1] == "", "legacy defaults")
        check(state["platform"] == "x" and state["media"] == "video" and state.get("audioQuality") == "auto" and len(state["files"]) == 1, "video quality normalized in safe status")
        check(state["title"] == "Veylo 合成标题第二行", "JSON title marker")
        check(set(state) == STATUS_KEYS, "status whitelist")
        check(state["sponsorblock"] == "off" and state["section"] == "" and state["playlist"] is False and state["items"] == ""
              and state["live"] == "off" and state["fileCount"] == 1 and state["warnings"] == [], "new status fields default for plain job")
        persisted = json.loads((native / "jobs" / (accepted["jobId"] + ".json")).read_text(encoding="utf-8"))
        check(all(persisted[k] == v for k, v in {"media": "video", "audioQuality": "auto", "subtitles": "off", "subLangs": "zh.*,en", "metadata": False, "part": "current"}.items()), "persisted normalized defaults")
        opened = call(exe, {"action": "open-job-folder", "jobId": accepted["jobId"]}, env)
        check(opened["ok"] and opened["selected"], "BRIDGE_TEST folder select without Explorer")

        for platform in ("x", "youtube"):
            for media in ("audio-mp3", "audio-m4a"):
                accepted = accept(202 if platform == "x" else None, media=media, quality="480", metadata=True)
                state, _ = wait(exe, accepted, env)
                args = invocation()
                check(state["state"] == "completed" and state["media"] == media and state.get("audioQuality") == "auto", "omitted audio quality defaults auto " + platform + "/" + media)
                check(args[args.index("-f") + 1] == "ba/b" and "-x" in args and args[args.index("--audio-format") + 1] == media[6:], "audio-only selector/extraction")
                check(args[args.index("--audio-quality") + 1] == "0", "audio quality")
                check(not any(a in args for a in ("--format", "--merge-output-format", "--remux-video", "--write-subs")), "no video format/remux/subtitles on audio")
                check("--embed-metadata" in args and "--embed-chapters" in args and Path(state["files"][0]).suffix == "." + media[6:], "audio metadata and extension")

        encoded_log = app / "ffmpeg-invocations.jsonl"
        check(not encoded_log.exists(), "auto audio and video never invoke direct encoder")
        accepted = accept(300, media="audio-mp3", audioQuality="192", metadata=True)
        duplicate = accept(300, media="audio-mp3", audioQuality="192", metadata=True)
        check(duplicate.get("jobId") == accepted.get("jobId"), "same explicit quality dedupes")
        state, _ = wait(exe, accepted, env)
        args = invocation()
        check(state["state"] == "completed" and state.get("audioQuality") == "192", "explicit bitrate completes with quality in status")
        check(args[args.index("-f") + 1] == "ba/b" and not any(a in args for a in ("-x", "--audio-format", "--audio-quality", "--remux-video", "--merge-output-format")),
              "explicit bitrate downloads original audio without copy-skipping extraction")
        check("--embed-metadata" in args and "--embed-chapters" in args, "explicit metadata preserved at download boundary")
        stage = Path(args[args.index("--paths") + 1])
        check(stage != downloads and stage.is_relative_to(native / "jobs"), "explicit source lives in private per-job staging")
        check(encoded_log.exists(), "explicit bitrate runs FFmpeg encoder")
        encoded = [json.loads(line) for line in encoded_log.read_text(encoding="utf-8").splitlines()]
        check(len(encoded) == 1, "one direct encode per source")
        enc = encoded[0]
        check(enc[enc.index("-c:a") + 1] == "libmp3lame" and enc[enc.index("-b:a") + 1] == "192k" and "-vn" in enc,
              "explicit MP3 codec/bitrate rather than quality-only hint")
        check(enc[enc.index("-map_metadata") + 1] == "0" and enc[enc.index("-map_chapters") + 1] == "0", "final encode retains requested tags/chapters")
        check(len(state["files"]) == 1 and Path(state["files"][0]).parent == downloads
              and Path(state["files"][0]).name == "original, 音源 [clip] [192k].mp3", "publish only final original-name quality-suffixed output")
        check(not stage.exists() and all(str(stage) not in p for p in state["files"]), "private source removed and never published")

        for media, codec in (("audio-mp3", "libmp3lame"), ("audio-m4a", "aac")):
            for quality in ("320", "256", "192", "128"):
                before = len(encoded_log.read_text(encoding="utf-8").splitlines())
                accepted = accept(310, media=media, audioQuality=quality)
                state, _ = wait(exe, accepted, env)
                enc = json.loads(encoded_log.read_text(encoding="utf-8").splitlines()[-1])
                check(state["state"] == "completed" and state.get("audioQuality") == quality and state["media"] == media,
                      "explicit quality matrix " + media + "/" + quality)
                check(len(encoded_log.read_text(encoding="utf-8").splitlines()) == before + 1 and enc[enc.index("-c:a") + 1] == codec
                      and enc[enc.index("-b:a") + 1] == quality + "k" and "-n" in enc, "exactly one explicit codec/bitrate encode")
                check(enc[enc.index("-map_metadata") + 1] == "-1" and enc[enc.index("-map_chapters") + 1] == "-1", "metadata opt-out is preserved")
                stored = json.loads((native / "jobs" / (accepted["jobId"] + ".json")).read_text(encoding="utf-8"))
                check(stored.get("audioQuality") == quality and stored["files"] == state["files"], "explicit quality persisted on original job")

        before = len(encoded_log.read_text(encoding="utf-8").splitlines())
        env["SG_TEST_MODE"] = "multi-source"
        multi, _ = wait(exe, accept(320, media="audio-m4a", audioQuality="256"), env)
        env.pop("SG_TEST_MODE")
        check(multi["state"] == "completed" and len(multi["files"]) == 2, "X multi-SGFILEJ completes all audio sources")
        check(len(encoded_log.read_text(encoding="utf-8").splitlines()) == before + 2, "duplicate source marker does not double encode")
        check({Path(p).name for p in multi["files"]} == {"original, 音源 [clip] [256k] (1).m4a", "second, 音源 [clip2] [256k].m4a"},
              "multiple original filenames retained with collision suffix")
        check(all(Path(p).parent == downloads and Path(p).stat().st_size for p in multi["files"]), "multi status only valid final files")

        existing = downloads / "original, 音源 [clip] [128k].mp3"
        original_bytes = existing.read_bytes()
        env["SG_TEST_MODE"] = "source-announced"
        pending = accept(321, media="audio-mp3", audioQuality="128")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            args = invocation()
            if args[-1].endswith("/321"):
                stage = Path(args[args.index("--paths") + 1])
                if (stage / "original, 音源 [clip].mp4").exists():
                    break
            time.sleep(.04)
        else:
            raise AssertionError("source fixture never announced")
        interim = call(exe, {"action": "job-status", "jobId": pending["jobId"]}, env)["job"]
        check(interim["state"] == "downloading" and interim["files"] == [] and interim["percent"] < 100,
              "source marker cannot announce a completed output")
        final, _ = wait(exe, pending, env)
        env.pop("SG_TEST_MODE")
        check(final["state"] == "completed" and Path(final["files"][0]).name == "original, 音源 [clip] [128k] (1).mp3"
              and existing.read_bytes() == original_bytes, "quality collision never overwrites existing user output")

        unrelated = native / "jobs" / "unrelated.audio-stage"
        unrelated.mkdir()
        sentinel = unrelated / "keep.txt"
        sentinel.write_bytes(b"not owned by this job")
        for index, mode in enumerate(("ffmpeg-fail", "ffmpeg-empty", "ffmpeg-missing", "download-fail", "empty-source", "no-source",
                                      "outside-source", "traversal-source", "relative-source", "bad-marker")):
            before = len(encoded_log.read_text(encoding="utf-8").splitlines())
            old_files = {str(p): p.read_bytes() for p in downloads.iterdir() if p.is_file()}
            env["SG_TEST_MODE"] = mode
            failed, _ = wait(exe, accept(330 + index, media="audio-m4a", audioQuality="192"), env)
            env.pop("SG_TEST_MODE")
            args = invocation()
            stage = Path(args[args.index("--paths") + 1])
            check(failed["state"] == "failed" and failed["files"] == [] and failed["percent"] < 100, "encoding/download/path failure never completes " + mode)
            check(not stage.exists() and sentinel.read_bytes() == b"not owned by this job" and (app / "outside.mp4").read_bytes() == b"not in selected directory",
                  "cleanup only exact owned staging " + mode)
            check({str(p): p.read_bytes() for p in downloads.iterdir() if p.is_file()} == old_files, "failure leaves existing selected files unchanged " + mode)
            if not mode.startswith("ffmpeg-"):
                check(len(encoded_log.read_text(encoding="utf-8").splitlines()) == before, "invalid source/downloader failure never invokes encoder " + mode)

        probe_source = fixture / "MockFfprobe.cs"
        probe_source.write_text(MOCK_FFPROBE, encoding="utf-8")
        compile_cs(probe_source, app / "bin/ffmpeg/ffprobe.exe")
        before = len(encoded_log.read_text(encoding="utf-8").splitlines())
        accepted = accept(600, media="audio-original", audioQuality="320", metadata=True)
        duplicate = accept(600, media="audio-original", audioQuality="auto", metadata=True)
        check(duplicate.get("jobId") == accepted.get("jobId"), "valid fixed original quality normalizes before dedup")
        original, _ = wait(exe, accepted, env)
        check(original["state"] == "completed" and original["media"] == "audio-original" and original["audioQuality"] == "auto", "original mode completes with normalized safe status")
        args = invocation()
        stage = Path(args[args.index("--paths") + 1])
        check(args[args.index("-f") + 1] == "ba/b" and not any(a in args for a in ("-x", "--audio-format", "--audio-quality", "--remux-video", "--merge-output-format")),
              "original downloads best available source without extract/transcode postprocessor")
        enc = json.loads(encoded_log.read_text(encoding="utf-8").splitlines()[-1])
        check(len(encoded_log.read_text(encoding="utf-8").splitlines()) == before + 1 and enc[enc.index("-c:a") + 1] == "copy"
              and enc[enc.index("-map") + 1] == "0:a:0" and "-vn" in enc, "original maps first audio by stream copy")
        check(not any(a in enc for a in ("-b:a", "-ar", "-ac", "-af", "-filter:a", "-filter_complex")), "original has no bitrate resampling remix or filter options")
        check(len(original["files"]) == 1 and Path(original["files"][0]).parent == downloads
              and Path(original["files"][0]).name == "original, 音源 [clip] [original].m4a" and not stage.exists(), "original codec-specific final name and private stage cleanup")
        probes = [json.loads(line) for line in (app / "ffprobe-invocations.jsonl").read_text(encoding="utf-8").splitlines()]
        check(len(probes) == 1 and probes[0][probes[0].index("-select_streams") + 1] == "a:0"
              and probes[0][probes[0].index("-show_entries") + 1] == "stream=codec_name"
              and probes[0][probes[0].index("-of") + 1] == "json", "extension chosen from first audio codec JSON only")

        mappings = [("aac", False, ".m4a"), ("alac", False, ".m4a"), ("opus", False, ".opus"), ("mp3", False, ".mp3"),
                    ("flac", False, ".flac"), ("vorbis", False, ".ogg"), ("ac3", False, ".ac3"), ("eac3", False, ".eac3"),
                    ("ac3", True, ".mka"), ("eac3", True, ".mka"), ("unlisted_codec", False, ".mka")]
        for index, (codec, metadata_enabled, extension) in enumerate(mappings):
            env["SG_TEST_CODEC"] = codec
            accepted = accept(610 + index, media="audio-original", audioQuality=("128", "192", "256", "320")[index % 4], metadata=metadata_enabled)
            state, _ = wait(exe, accepted, env)
            enc = json.loads(encoded_log.read_text(encoding="utf-8").splitlines()[-1])
            check(state["state"] == "completed" and state["audioQuality"] == "auto" and Path(state["files"][0]).suffix == extension,
                  "codec container mapping " + codec + "/metadata=" + str(metadata_enabled))
            check(enc[enc.index("-c:a") + 1] == "copy" and "-b:a" not in enc and "-ar" not in enc and "-ac" not in enc, "all original codec mappings stay copy-only")
            stored = json.loads((native / "jobs" / (accepted["jobId"] + ".json")).read_text(encoding="utf-8"))
            check(stored["media"] == "audio-original" and stored["audioQuality"] == "auto" and stored["files"] == state["files"], "original normalized request persisted on original job")
        env.pop("SG_TEST_CODEC")
        preserved = {str(p): p.read_bytes() for p in downloads.glob("* [[]original]*") if p.is_file()}
        check(preserved, "original collision fixture must include preexisting outputs")
        before = len(encoded_log.read_text(encoding="utf-8").splitlines())
        env["SG_TEST_MODE"] = "multi-source"
        state, _ = wait(exe, accept(630, media="audio-original"), env)
        env.pop("SG_TEST_MODE")
        check(state["state"] == "completed" and len(state["files"]) == 2 and all(Path(p).suffix == ".m4a" for p in state["files"]), "original X multi-source uses copied audio outputs only")
        check(len(encoded_log.read_text(encoding="utf-8").splitlines()) == before + 2, "original duplicate markers never duplicate processing")
        check(all(Path(p).read_bytes() == content for p, content in preserved.items()) and not set(state["files"]) & set(preserved), "original collision publication preserves all preexisting outputs")
        env["SG_TEST_MODE"] = "source-announced"
        accepted = accept(631, media="audio-original")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            args = invocation()
            if args[-1].endswith("/631") and (Path(args[args.index("--paths") + 1]) / "original, 音源 [clip].mp4").exists():
                break
            time.sleep(.04)
        else:
            raise AssertionError("original source fixture never announced")
        interim = call(exe, {"action": "job-status", "jobId": accepted["jobId"]}, env)["job"]
        check(interim["state"] == "downloading" and interim["files"] == [] and interim["percent"] < 100, "original source markers stay private before downloader success")
        check(wait(exe, accepted, env)[0]["state"] == "completed", "original completes only after successful copy and publication")
        env.pop("SG_TEST_MODE")
        for index, mode in enumerate(("probe-fail", "probe-json", "probe-no-audio", "ffmpeg-fail", "ffmpeg-empty", "ffmpeg-missing",
                                      "download-fail", "outside-source", "traversal-source", "relative-source", "bad-marker", "empty-source", "no-source")):
            before = len(encoded_log.read_text(encoding="utf-8").splitlines())
            old_files = {str(p): p.read_bytes() for p in downloads.iterdir() if p.is_file()}
            env["SG_TEST_MODE"] = mode
            state, _ = wait(exe, accept(640 + index, media="audio-original"), env)
            env.pop("SG_TEST_MODE")
            args = invocation()
            stage = Path(args[args.index("--paths") + 1])
            check(state["state"] == "failed" and state["files"] == [] and state["percent"] < 100, "original failure never completes " + mode)
            check(not stage.exists() and sentinel.read_bytes() == b"not owned by this job" and (app / "outside.mp4").read_bytes() == b"not in selected directory", "original only cleans its own stage " + mode)
            check({str(p): p.read_bytes() for p in downloads.iterdir() if p.is_file()} == old_files, "original failure never overwrites outputs " + mode)
            if mode.startswith("probe-"):
                check("ffprobe.exe" in state["error"] and "synthetic-private-value" not in state["error"], "original probe failure has safe actionable error")
            if mode == "ffmpeg-fail":
                check("未进行转码" in state["error"], "unsupported copy explicitly fails without fallback transcode")
            if not mode.startswith("ffmpeg-"):
                check(len(encoded_log.read_text(encoding="utf-8").splitlines()) == before, "failed original source/probe never invokes FFmpeg " + mode)

        for mode, lang in (("manual", "zh.*,en"), ("manual", "zh.*"), ("auto", "en")):
            accepted = accept(subtitles=mode, subLangs=lang, metadata=True)
            state, _ = wait(exe, accepted, env)
            args = invocation()
            check(state["state"] == "completed" and len(state["files"]) == 2, "video/subtitle completed paths")
            check("--write-subs" in args and ("--write-auto-subs" in args) == (mode == "auto"), "subtitle mode flags")
            check(args[args.index("--sub-langs") + 1] == lang and args[args.index("--convert-subs") + 1] == "srt" and "--embed-subs" not in args, "external SRT whitelist")
            check("--embed-metadata" in args and "--embed-chapters" in args, "video metadata")
        subonly, _ = wait(exe, accept(url="https://www.youtube.com/watch?v=subonly0000", subtitles="manual"), env)
        check(subonly["state"] == "failed", "subtitle-only marker cannot complete a media job")
        legacy_state, _ = wait(exe, accept(901), env)
        check(legacy_state["state"] == "completed" and len(legacy_state["files"]) == 1, "legacy SGFILE marker")
        for num in (9998, 9997, 9999):
            failure, _ = wait(exe, accept(num), env)
            check(failure["state"] == "failed" and "synthetic-private-value" not in failure["error"], "safe failure reporting/no missing output success")

        # A synthetic recent legacy record dedupes only when every option matches.
        seeded_id = uuid.uuid4().hex
        job_path = native / "jobs" / (seeded_id + ".json")
        seeded = {"id": seeded_id, "url": base["url"], "quality": "best", "downloadDir": str(downloads),
                  "state": "queued", "percent": 0, "pid": 0, "cookieStatus": "guest",
                  "updated": 621355968000000000 + int(time.time() * 10000000)}
        job_path.write_text(json.dumps(seeded), encoding="utf-8")
        check(accept().get("jobId") == seeded_id and accept(audioQuality="auto").get("jobId") == seeded_id,
              "missing legacy audioQuality matches explicit auto")
        check(accept(audioQuality="128").get("jobId") == seeded_id, "valid video bitrate normalized before dedup")
        legacy_status = call(exe, {"action": "job-status", "jobId": seeded_id}, env)["job"]
        check(legacy_status.get("audioQuality") == "auto", "legacy status explicitly defaults auto")
        # A warmed host must use ReadJob's existing retry when an atomic replacement is briefly absent.
        with subprocess.Popen([str(exe), ORIGIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as host:
            host.stdin.write(frame({"action": "ping"}))
            host.stdin.flush()
            size = struct.unpack("<I", host.stdout.read(4))[0]
            check(json.loads(host.stdout.read(size))["ok"], "warm status reader")
            held = job_path.with_suffix(".held")
            job_path.rename(held)
            try:
                host.stdin.write(frame({"action": "job-status", "jobId": seeded_id}))
                host.stdin.flush()
                time.sleep(.04)
            finally:
                held.rename(job_path)
            wire, errors = host.communicate(timeout=15)
            size = struct.unpack("<I", wire[:4])[0]
            check(host.returncode == 0 and not errors and json.loads(wire[4:4 + size]).get("ok") is True,
                  "status retries temporary job record absence during atomic replace")
        missing = call(exe, {"action": "job-status", "jobId": uuid.uuid4().hex}, env)
        check(missing.get("ok") is False and missing.get("error") == "找不到此下载任务。", "truly missing job retains safe error")
        seeded["media"] = "audio-mp3"
        job_path.write_text(json.dumps(seeded), encoding="utf-8")
        check(accept(media="audio-mp3").get("jobId") == seeded_id
              and accept(media="audio-mp3", audioQuality="auto").get("jobId") == seeded_id, "legacy audio missing quality dedupes as auto")
        explicit = accept(media="audio-mp3", audioQuality="128")
        check(explicit.get("jobId") != seeded_id, "explicit bitrate does not dedupe with legacy auto audio")
        check(wait(exe, explicit, env)[0]["state"] == "completed", "distinct legacy-audio quality completes")
        seeded["media"] = "audio-original"
        job_path.write_text(json.dumps(seeded), encoding="utf-8")
        check(accept(media="audio-original").get("jobId") == seeded_id
              and accept(media="audio-original", audioQuality="256").get("jobId") == seeded_id, "original missing and normalized fixed qualities dedupe as auto")
        check(call(exe, {"action": "job-status", "jobId": seeded_id}, env)["job"]["audioQuality"] == "auto", "legacy original status defaults quality auto")
        distinct = accept(media="audio-mp3", audioQuality="auto")
        check(distinct.get("jobId") != seeded_id and wait(exe, distinct, env)[0]["state"] == "completed", "original identity stays distinct from forced MP3 auto")
        seeded.pop("media")
        job_path.write_text(json.dumps(seeded), encoding="utf-8")
        different = [accept(media="audio-mp3"), accept(subtitles="manual"), accept(subLangs="en"),
                     accept(metadata=True), accept(quality="480"), accept(dir=str(other_dir))]
        check(all(j.get("jobId") != seeded_id for j in different), "dedupe media/subtitles/languages/metadata/quality/directory")
        for j in different:
            check(wait(exe, j, env)[0]["state"] == "completed", "distinct option worker completes")
        seeded["state"] = "completed"
        seeded["error"] = "synthetic-private-value proxy credential"
        seeded["url"] += "&private=synthetic-private-value"
        seeded["proxy"] = "synthetic-private-value"
        seeded["cookieFile"] = "synthetic-private-value"
        seeded["percent"] = {"private": "synthetic-private-value"}
        safe = []
        for index in range(23):
            path = downloads / ("file-%d.mp4" % index)
            path.write_bytes(b"finished")
            safe.append(str(path))
        seeded["files"] = [str(app / "outside.mp4"), str(downloads / "missing.mp4"), str(downloads), *safe, safe[0]]
        job_path.write_text(json.dumps(seeded), encoding="utf-8")
        status = call(exe, {"action": "job-status", "jobId": seeded_id}, env)["job"]
        check(len(status["files"]) == 20 and all(Path(p).parent == downloads for p in status["files"]), "existing in-directory files max20")
        check("synthetic-private-value" not in json.dumps(status), "status omits credentials and canonicalizes URL/errors")
        check(call(exe, {"action": "open-job-folder", "jobId": seeded_id, "path": str(downloads)}, env)["ok"] is False, "open action does not accept a path")
        check(call(exe, {"action": "open-job-folder", "jobId": "../bad"}, env)["ok"] is False, "open invalid id")
        check(call(exe, {"action": "open-job-folder", "jobId": uuid.uuid4().hex}, env)["ok"] is False, "open missing job")
        for path in ("relative", "D:drive-relative", "\\\\server\\share", "\\\\?\\C:\\Windows", str(downloads / "missing"), safe[0], str(downloads) + "\t"):
            seeded["downloadDir"] = path
            job_path.write_text(json.dumps(seeded), encoding="utf-8")
            check(call(exe, {"action": "open-job-folder", "jobId": seeded_id}, env)["ok"] is False, "unsafe/non-directory folder rejected")
        junction = fixture / "local-junction"
        made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(other_dir)], capture_output=True, timeout=10)
        check(made.returncode == 0, "create isolated directory junction")
        try:
            seeded["downloadDir"] = str(junction)
            job_path.write_text(json.dumps(seeded), encoding="utf-8")
            check(call(exe, {"action": "open-job-folder", "jobId": seeded_id}, env)["ok"] is False, "junction is not an ordinary local folder")
            linked_file = other_dir / "linked.mp4"
            linked_file.write_bytes(b"synthetic")
            nested = downloads / "nested-junction"
            made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(nested), str(other_dir)], capture_output=True, timeout=10)
            check(made.returncode == 0, "create isolated nested junction")
            try:
                seeded.update(downloadDir=str(downloads), files=[str(nested / "linked.mp4")])
                job_path.write_text(json.dumps(seeded), encoding="utf-8")
                check(call(exe, {"action": "job-status", "jobId": seeded_id}, env)["job"]["files"] == [], "files through junction are not exposed")
            finally:
                os.rmdir(nested)
        finally:
            os.rmdir(junction)
        seeded["downloadDir"] = str(downloads)
        seeded["files"] = []
        job_path.write_text(json.dumps(seeded), encoding="utf-8")
        check(call(exe, {"action": "open-job-folder", "jobId": seeded_id}, env)["selected"] is False, "existing directory fallback without files")
        stale = {**seeded, "state": "downloading", "updated": 621355968000000000 + int((time.time() - 120) * 10000000)}
        job_path.write_text(json.dumps(stale), encoding="utf-8")
        check(call(exe, {"action": "job-status", "jobId": seeded_id}, env)["job"]["state"] == "failed", "stale-worker behavior")
        feature_tests(exe, env, app, native, downloads, fixture, accept, invocation, base, other_dir)
        # Real tools are read-only; every source/config/output below is a local synthetic fixture.
        desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
        category = desktop / "Applications"
        roots = [Path(os.environ["VEYLO_TEST_FLUENT_DIR"])] if os.environ.get("VEYLO_TEST_FLUENT_DIR") else [desktop / "FluentYTDL-3.7.6-win64-full"]
        if category.is_dir():
            roots.extend(category.glob("FluentYTDL-*-win64-full"))
        tools = next((root / "bin/ffmpeg" for root in roots
                      if (root / "bin/ffmpeg/ffmpeg.exe").is_file() and (root / "bin/ffmpeg/ffprobe.exe").is_file()), None)
        check(tools is not None, "known local full FFmpeg/FFprobe required for synthetic encode verification")
        audio = fixture / "synthetic-audio"
        audio.mkdir()
        metadata = audio / "tags.ffmetadata"
        metadata.write_text(";FFMETADATA1\ntitle=Offline audio fixture\nartist=Synthetic test\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=8000\ntitle=Local chapter\n", encoding="utf-8")
        real_ffmpeg = tools / "ffmpeg.exe"
        ffprobe = tools / "ffprobe.exe"

        def probe(path):
            result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-show_chapters", "-of", "json", str(path)],
                                    capture_output=True, timeout=20)
            check(result.returncode == 0, "FFprobe reads synthetic audio")
            return json.loads(result.stdout)

        for codec, extension in (("aac", "m4a"), ("libmp3lame", "mp3"), ("libopus", "opus")):
            source = audio / ("source." + extension)
            result = subprocess.run([str(real_ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-f", "lavfi", "-i",
                                     "aevalsrc=0.3*sin(2*PI*997*t)|0.3*sin(2*PI*1237*t):s=48000:d=10", "-f", "ffmetadata", "-i", str(metadata),
                                     "-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1", "-ac", "2", "-c:a", codec, "-b:a", "96k", str(source)],
                                    capture_output=True, timeout=30)
            check(result.returncode == 0, "generate synthetic " + extension + " input: " + result.stderr.decode("utf-8", "replace"))
        shutil.copy2(real_ffmpeg, app / "bin/ffmpeg/ffmpeg.exe")
        # AAC/MP3 same-codec inputs catch yt-dlp's copy/same-path shortcut; Opus verifies cross-codec input.
        real_cases = [(media, extension, quality) for media, extension in (("audio-m4a", "m4a"), ("audio-mp3", "mp3"))
                      for quality in ("320", "256", "192", "128")]
        real_cases += [("audio-mp3", "opus", "192"), ("audio-m4a", "opus", "192")]
        for index, (media, extension, quality) in enumerate(real_cases):
            env["SG_TEST_SOURCE"] = str(audio / ("source." + extension))
            accepted = accept(500 + index, media=media, audioQuality=quality, metadata=True)
            state, _ = wait(exe, accepted, env)
            check(state["state"] == "completed" and len(state["files"]) == 1, "real FFmpeg completes " + extension + " -> " + media + "/" + quality)
            info = probe(Path(state["files"][0]))
            streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
            expected_codec = "mp3" if media == "audio-mp3" else "aac"
            check(len(streams) == 1 and streams[0]["codec_name"] == expected_codec
                  and not any(s["codec_type"] == "video" for s in info["streams"]), "real output audio codec/no video")
            bitrate = int(streams[0]["bit_rate"]) / 1000
            # Native AAC is VBR on short sine fixtures; require clear target-dependent re-encoding, not exact CBR.
            tolerance = .18 if expected_codec == "aac" else .02
            check(abs(bitrate - int(quality)) <= int(quality) * tolerance, "real output bitrate " + str(bitrate) + " vs " + quality)
            tags = {key.lower(): value for key, value in info["format"].get("tags", {}).items()}
            check(tags.get("title") == "Offline audio fixture" and tags.get("artist") == "Synthetic test", "real encoded metadata retained")
            if extension != "opus":
                check(any(c.get("tags", {}).get("title") == "Local chapter" for c in info.get("chapters", [])), "real encoded chapters retained")
            check(all(Path(p).parent == downloads for p in state["files"]), "real output published inside selected folder")
            print("REAL", extension, "->", expected_codec, quality + "k", "observed", round(bitrate, 1), "kbps")
        env.pop("SG_TEST_SOURCE")
        cover = audio / "cover.jpg"
        image_result = subprocess.run([str(real_ffmpeg), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64",
                                       "-frames:v", "1", "-update", "1", str(cover)], capture_output=True, timeout=20)
        check(image_result.returncode == 0, "generate local thumbnail")
        env["SG_TEST_SOURCE"] = str(audio / "source.opus")
        env["SG_TEST_THUMB"] = str(cover)
        for index, media in enumerate(("audio-mp3", "audio-m4a")):
            covered, _ = wait(exe, accept(850 + index, media=media, audioQuality="192", embedThumbnail=True), env)
            check(covered["state"] == "completed", "fixed bitrate cover conversion completes")
            covered_info = probe(Path(covered["files"][0]))
            check(any(stream.get("disposition", {}).get("attached_pic") == 1 for stream in covered_info["streams"]), "fixed bitrate retains embedded cover")
            check(len([stream for stream in covered_info["streams"] if stream["codec_type"] == "audio"]) == 1, "cover adds no extra audio stream")
        env["SG_TEST_MODE"] = "missing-thumbnail"
        no_cover, _ = wait(exe, accept(852, media="audio-m4a", audioQuality="192", embedThumbnail=True), env)
        check(no_cover["state"] == "completed" and "没有可用封面，媒体已保存。" in no_cover["warnings"], "missing optional thumbnail preserves audio with visible warning")
        env.pop("SG_TEST_MODE"); env.pop("SG_TEST_SOURCE"); env.pop("SG_TEST_THUMB")
        # Stream-copy proof compares actual ordered compressed packets, not a guessed source bitrate.
        source_flac = audio / "source.flac"
        result = subprocess.run([str(real_ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-f", "lavfi", "-i",
                                 "sine=frequency=673:sample_rate=44100:duration=4", "-c:a", "flac", str(source_flac)], capture_output=True, timeout=30)
        check(result.returncode == 0, "generate lossless mono 44100Hz FLAC fixture")
        combined = audio / "combined.mp4"
        result = subprocess.run([str(real_ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-f", "lavfi", "-i",
                                 "color=c=black:size=16x16:rate=1:duration=2", "-i", str(audio / "source.m4a"), "-map", "0:v:0", "-map", "1:a:0",
                                 "-c:v", "mpeg4", "-c:a", "copy", str(combined)], capture_output=True, timeout=30)
        check(result.returncode == 0 and any(s["codec_type"] == "video" for s in probe(combined)["streams"]), "generate combined-video fallback fixture with copied AAC source")
        shutil.copy2(ffprobe, app / "bin/ffmpeg/ffprobe.exe")

        def packet_hashes(path):
            result = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "a:0", "-show_packets", "-show_entries", "packet=data_hash",
                                     "-show_data_hash", "sha256", "-of", "json", str(path)], capture_output=True, timeout=20)
            check(result.returncode == 0, "FFprobe hashes demuxed synthetic audio packets")
            packets = json.loads(result.stdout)["packets"]
            check(packets and all(p.get("data_hash", "").startswith("SHA256:") for p in packets), "packet proof contains actual compressed payload hashes")
            return [p["data_hash"] for p in packets]

        for index, (source_name, expected_extension) in enumerate((("source.m4a", ".m4a"), ("source.opus", ".opus"), ("source.mp3", ".mp3"),
                                                                 ("source.flac", ".flac"), ("combined.mp4", ".m4a"))):
            source = audio / source_name
            env["SG_TEST_SOURCE"] = str(source)
            state, _ = wait(exe, accept(700 + index, media="audio-original", audioQuality="320", metadata=True), env)
            check(state["state"] == "completed" and state["audioQuality"] == "auto" and len(state["files"]) == 1, "real original mode completes " + source_name)
            output = Path(state["files"][0])
            before_info, after_info = probe(source), probe(output)
            before_audio = next(s for s in before_info["streams"] if s["codec_type"] == "audio")
            after_audio = [s for s in after_info["streams"] if s["codec_type"] == "audio"]
            check(len(after_audio) == 1 and all(after_audio[0][key] == before_audio[key] for key in ("codec_name", "sample_rate", "channels")),
                  "original preserves source codec/sample-rate/channel count " + source_name)
            check(not any(s["codec_type"] == "video" for s in after_info["streams"]) and output.suffix == expected_extension
                  and output.parent == downloads, "original audio-only customary container published safely " + source_name)
            source_packets, output_packets = packet_hashes(source), packet_hashes(output)
            check(source_packets == output_packets, "original ordered compressed audio packet payloads are byte-identical " + source_name)
            print("ORIGINAL", source_name, "->", output.suffix, before_audio["codec_name"], before_audio["sample_rate"], "Hz",
                  before_audio["channels"], "channels; identical SHA256 payloads", len(source_packets), "packets")
        env.pop("SG_TEST_SOURCE")
        if options.release_output:
            check(not options.release_output.exists(), "release staging output must be new")
            options.release_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(release, options.release_output)
            print("STAGED", str(options.release_output))
        time.sleep(.2)
    print("PASS Shiguang Veylo offline:", CHECKS, "assertions; no network/profile/Cookie/Explorer access")


if __name__ == "__main__":
    main()
