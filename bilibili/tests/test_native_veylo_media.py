"""Isolated native/driver regression; no real account, browser or network is used."""
import argparse
import ctypes
from contextlib import redirect_stderr
from enum import IntEnum, IntFlag
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
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


def call(exe, message, env):
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    result = subprocess.run([str(exe), ORIGIN], input=struct.pack("<I", len(body)) + body,
                            env=env, capture_output=True, timeout=15)
    check(result.returncode == 0 and not result.stderr, "native exit/stdout")
    check(len(result.stdout) >= 4, "native header")
    size = struct.unpack("<I", result.stdout[:4])[0]
    check(len(result.stdout) == size + 4, "native frame length")
    return json.loads(result.stdout[4:])


def wait(exe, accepted, env):
    check(accepted.get("ok") is True, "fixture accepted")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = call(exe, {"action": "job-status", "jobId": accepted["jobId"]}, env)
        check(response.get("ok") is True, "fixture status: " + repr(response))
        if response["job"]["state"] in ("completed", "failed", "canceled"):
            time.sleep(.15)
            return call(exe, {"action": "job-status", "jobId": accepted["jobId"]}, env)["job"]
        time.sleep(.1)
    raise AssertionError("isolated Bili worker did not finish")


MOCK = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;
internal static class OfflinePython {
    static string Option(string[] args, string key) {
        int i = Array.IndexOf(args, key); return i >= 0 ? args[i + 1] : "";
    }
    static void Save(JavaScriptSerializer json, string jobFile, Dictionary<string, object> job) {
        job["updated"] = DateTime.UtcNow.Ticks;
        string temp = jobFile + ".offline.tmp";
        File.WriteAllText(temp, json.Serialize(job));
        File.Replace(temp, jobFile, null);
    }
    static int CancelScenario(string[] args, JavaScriptSerializer json, string root) {
        // Long-running engine with its own child process and private artefacts.
        string id = Option(args, "--job-id"), jobs = Option(args, "--jobs-dir"), output = Option(args, "--output-dir");
        string jobFile = Path.Combine(jobs, id + ".json");
        string work = Path.Combine(jobs, id + ".work");
        Directory.CreateDirectory(Path.Combine(work, "nested"));
        File.WriteAllText(Path.Combine(work, "nested", "video.m4s"), "partial");
        string original = Path.Combine(output, ".veylo-original-" + id + "-tmp1");
        Directory.CreateDirectory(original);
        File.WriteAllText(Path.Combine(original, "complete.m4a"), "partial");
        string sub = Path.Combine(output, "合集 子目录");
        Directory.CreateDirectory(sub);
        File.WriteAllText(Path.Combine(sub, ".veylo-partial-" + id + "-ep2.mp4"), "partial");
        string done = Path.Combine(sub, "ep1 完成.mp4");
        File.WriteAllText(done, "complete");
        string self = System.Reflection.Assembly.GetExecutingAssembly().Location;
        string childPid = Path.Combine(root, "cancel-child-" + id + ".pid");
        var child = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo {
            FileName = self, Arguments = "--child \"" + childPid + "\"", UseShellExecute = false, CreateNoWindow = true });
        File.WriteAllText(Path.Combine(root, "cancel-driver-" + id + ".pid"), System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
        var job = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(jobFile));
        job["state"] = "downloading"; job["percent"] = 37.5; job["title"] = "取消合成"; job["files"] = new [] { done };
        Save(json, jobFile, job);
        for (int i = 0; i < 100 && !File.Exists(childPid); i++) Thread.Sleep(50);
        File.WriteAllText(Path.Combine(root, "cancel-ready-" + id), "ready");
        Thread.Sleep(120000);
        // Only reached when not killed: would wrongly overwrite the canceled state.
        job = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(jobFile));
        job["state"] = "completed"; job["percent"] = 100;
        Save(json, jobFile, job);
        return 0;
    }
    static int Main(string[] args) {
        if (args.Length == 2 && args[0] == "--child") {
            File.WriteAllText(args[1], System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
            Thread.Sleep(120000);
            return 0;
        }
        var json = new JavaScriptSerializer();
        string root = Option(args, "--bili-root"), url = Option(args, "--url");
        File.WriteAllText(Path.Combine(root, "invocation.json"), json.Serialize(args));
        if (url.EndsWith("av9990")) return CancelScenario(args, json, root);
        Thread.Sleep(600);
        string jobFile = Path.Combine(Option(args, "--jobs-dir"), Option(args, "--job-id") + ".json");
        var job = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(jobFile));
        string media = Option(args, "--media");
        string file = Path.Combine(Option(args, "--output-dir"), "Bili, 合成 " + Option(args, "--job-id") + (media == "audio-mp3" ? ".mp3" : media == "audio-m4a" || media == "audio-original" ? ".m4a" : ".mp4"));
        File.WriteAllText(file, "offline synthetic media");
        job["title"] = "Bili 合成标题\n完成";
        job["files"] = new [] { file, Path.Combine(root, "outside.mp4") };
        job["driverExtra"] = "preserved";
        job["percent"] = 86;
        job["state"] = url.EndsWith("av9999") || url.EndsWith("av9998") ? "downloading" : "completed";
        if (url.EndsWith("av9997")) { job["state"] = "failed"; job["error"] = "synthetic-private-value proxy"; }
        if (url.EndsWith("av9996")) { job["state"] = "failed"; job["error"] = "指定音频码率需要支持 aac/libmp3lame 的完整 FFmpeg；请准备完整编码器或改选自动音质后重试。"; }
        if (url.EndsWith("av9995")) { job["state"] = "failed"; job["error"] = "原音轨需要现有完整 FFmpeg 和同目录 ffprobe；请准备工具后重试，或选择 MP3/M4A 转换。"; }
        if (url.EndsWith("av9994")) { job["state"] = "failed"; job["error"] = "无法确认或无损封装此原音轨；请检查媒体流，或选择 MP3/M4A 转换。"; }
        job["updated"] = DateTime.UtcNow.Ticks;
        string temp = jobFile + ".offline.tmp";
        File.WriteAllText(temp, json.Serialize(job));
        File.Replace(temp, jobFile, null);
        return url.EndsWith("av9999") ? 2 : 0;
    }
}
'''


class MediaType(IntEnum):
    UNKNOWN = 0
    DASH = 1
    MP4 = 2
    FLV = 3
    M4A = 4


class DownloadType(IntFlag):
    VIDEO = 1
    AUDIO = 2


class TaskInfo:
    def __init__(self):
        self.Basic = SimpleNamespace()
        self.Episode = SimpleNamespace()
        self.Download = SimpleNamespace(type=DownloadType.VIDEO | DownloadType.AUDIO,
                                        media_type=MediaType.DASH, video_quality_id=200)
        self.File = SimpleNamespace(relative_files=[])


def load_driver(fixture):
    sys.dont_write_bytecode = True
    path = fixture / "bd_driver.py"
    shutil.copy2(ROOT / "driver/bd_driver.py", path)
    spec = importlib.util.spec_from_file_location("veylo_driver_isolated", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def modules_for_driver():
    modules = {}
    for name in ("util", "util.common", "util.download", "util.download.task", "util.download.parse",
                 "util.download.downloader", "util.format", "util.format.file_name", "util.common.enum",
                 "util.download.task.info", "util.download.parse.video_info", "util.download.parse.audio_info",
                 "util.download.downloader.parse_worker", "util.download.parse.query_worker"):
        modules[name] = ModuleType(name)
    modules["util.common.enum"].MediaType = MediaType
    modules["util.common.enum"].DownloadType = DownloadType
    modules["util.download.task.info"].TaskInfo = TaskInfo
    return modules


def job_read_contention_tests(fixture, exe, env, downloads):
    """A briefly exclusive writer must not turn an existing job into 'not found'."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    job_id = uuid.uuid4().hex
    path = exe.parent / "jobs" / (job_id + ".json")
    media = downloads / "read-contention.mp4"
    media.write_bytes(b"synthetic")
    path.write_text(json.dumps({"id": job_id, "state": "completed", "percent": 100,
        "downloadDir": str(downloads), "files": [str(media)], "url": "https://www.bilibili.com/video/av1"}), encoding="utf-8")
    process = subprocess.Popen([str(exe), ORIGIN], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def request(message):
        body = json.dumps(message).encode("utf-8")
        process.stdin.write(struct.pack("<I", len(body)) + body)
        process.stdin.flush()
        header = process.stdout.read(4)
        check(len(header) == 4, "contention response header")
        return json.loads(process.stdout.read(struct.unpack("<I", header)[0]))
    try:
        check(request({"action": "ping"})["ok"], "contention host ready")
        for mode in ("exclusive-lock", "replacement-gap"):
            for action in ("job-status", "open-job-folder", "open-job-file", "cancel-job"):
                if mode == "exclusive-lock":
                    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
                    check(handle not in (None, ctypes.c_void_p(-1).value), "exclusive fixture lock")
                    release = threading.Timer(.075, kernel.CloseHandle, args=(handle,))
                else:
                    replacement = path.with_suffix(".replacement")
                    path.rename(replacement)
                    release = threading.Timer(.075, replacement.replace, args=(path,))
                release.start()
                try:
                    response = request({"action": action, "jobId": job_id,
                                        **({"fileIndex": 0} if action == "open-job-file" else {})})
                finally:
                    release.join()
                check(response.get("ok") is True, "existing job survives " + mode + ": " + action + " " + repr(response))
        missing = request({"action": "job-status", "jobId": uuid.uuid4().hex})
        check(missing.get("ok") is False and "找不到此下载任务" in missing.get("error", ""), "truly missing job remains rejected")
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        path.unlink()
        media.unlink()


def native_tests(fixture, release_output):
    native = fixture / "native"
    native.mkdir()
    driver_dir = fixture / "driver"
    driver_dir.mkdir()
    (driver_dir / "bd_driver.py").write_text("# never executed by offline interpreter\n", encoding="utf-8")
    app = fixture / "Bili23"
    (app / "runtime").mkdir(parents=True)
    (app / "bundle").mkdir()
    (app / "Bili23.exe").write_bytes(b"offline placeholder")
    (app / "bundle/ffmpeg.exe").write_bytes(b"offline placeholder")
    (app / "outside.mp4").write_bytes(b"not in selected directory")
    downloads = fixture / "下载, Bili"
    downloads.mkdir()
    alternate = fixture / "alternate"
    alternate.mkdir()
    (native / "bili_paths.json").write_text(json.dumps({"bili_root": str(app)}), encoding="utf-8")
    cfg = fixture / "isolated_config.json"
    cfg.write_text(json.dumps({"Download": {"download_path": str(downloads)}, "Cookie": {"is_login": False}}), encoding="utf-8")
    mock = fixture / "OfflinePython.cs"
    mock.write_text(MOCK, encoding="utf-8")
    compile_cs(mock, app / "runtime/python.exe")
    exe = native / "BiliBridge.exe"
    release = fixture / "release/BiliBridge.exe"
    release.parent.mkdir()
    source = fixture / "BiliBridge.cs"
    shutil.copy2(ROOT / "native/BiliBridge.cs", source)
    compile_cs(source, exe, True)
    compile_cs(source, release)
    # A normal release ping reads %APPDATA%; never execute that against a real profile.
    # Change only the two config paths in a disposable release-source fixture.
    isolated_source = fixture / "BiliBridgeReleaseIsolated.cs"
    config_expression = (r'Path\.Combine\(Environment\.GetFolderPath\(Environment\.SpecialFolder\.ApplicationData\),'
                         r'\s*"Bili23 Downloader", "config\.json"\)')
    source_text = source.read_text(encoding="utf-8")
    check(len(re.findall(config_expression, source_text)) == 2, "both release config paths are isolated before execution")
    isolated_text = re.sub(config_expression, lambda match: '@"' + str(cfg).replace('"', '""') + '"', source_text)
    isolated_source.write_text(isolated_text, encoding="utf-8")
    isolated_release = fixture / "release-isolated/BiliBridge.exe"
    isolated_release.parent.mkdir()
    compile_cs(isolated_source, isolated_release)
    env = dict(os.environ, BILI_TEST_CONFIG=str(cfg))
    release_ping = call(isolated_release, {"action": "ping"}, env)
    check(release_ping["version"] == "1.5.1" and release_ping.get("audioQualityOptions") == ["auto", "320", "256", "192", "128"], "release candidate ping contract")
    release_invalid = call(release, {"action": "bili-download", "url": "https://www.bilibili.com/video/BV1GJ411x7h7", "audioQuality": None}, env)
    check(release_invalid.get("ok") is False and "audioQuality" in release_invalid.get("error", ""), "release rejects invalid audioQuality before reading any real configuration")
    ping = call(exe, {"action": "ping"}, env)
    job_read_contention_tests(fixture, exe, env, downloads)
    check(ping["version"] == "1.5.1" and ping["engineAvailable"], "Bili ping version and isolated engine")
    check(ping.get("audioQualityOptions") == ["auto", "320", "256", "192", "128"], "Bili advertises audio quality contract")
    check(ping.get("mediaOptions") == ["video", "audio-original", "audio-mp3", "audio-m4a"]
          and release_ping.get("mediaOptions") == ping["mediaOptions"], "Bili advertises exact original media contract in test/release")
    assembly = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "[Reflection.AssemblyName]::GetAssemblyName('" + str(release).replace("'", "''") + "').Version.ToString()"],
        capture_output=True, timeout=15)
    check(assembly.returncode == 0 and assembly.stdout.decode().strip() == "1.5.1.0", "Bili assembly version")
    base = {"action": "bili-download", "url": "https://www.bilibili.com/video/BV1GJ411x7h7"}
    for changed in ({"media": "mp3"}, {"media": 1}, {"media": None}, {"media": "audio-mp3 --exec bad"},
                    {"subtitles": "manual"}, {"subtitles": "auto", "media": "audio-m4a"}, {"subtitles": True},
                    {"subLangs": "all"}, {"subLangs": ["en"]}, {"metadata": "true"}, {"metadata": 1},
                    {"quality": "1080"}, {"part": "bad"}, {"url": "https://www.bilibili.com.evil.invalid/video/BV1GJ411x7h7"},
                    {"url": "https://user:password@www.bilibili.com/video/BV1GJ411x7h7"}):
        check(call(exe, {**base, **changed}, env)["ok"] is False, "Bili invalid option rejected")

    jobs = native / "jobs"
    before = set(jobs.iterdir())
    config = native / "bili_paths.json"
    config.write_text(json.dumps({"bili_root": str(fixture / "missing-engine")}), encoding="utf-8")
    try:
        for media in ("video", "audio-original", "audio-mp3", "audio-m4a"):
            for invalid in (None, 320, 128.0, True, False, [], {}, ["320"], "", "AUTO", "64", "320k", " 320", "320 ", "320\n", "320 --exec bad"):
                response = call(exe, {**base, "media": media, "audioQuality": invalid}, env)
                check(response.get("ok") is False and "audioQuality" in response.get("error", ""), "strict audioQuality rejected before engine/config/work")
        check(set(jobs.iterdir()) == before and not (app / "invocation.json").exists(), "invalid audioQuality never creates a job or starts driver")
    finally:
        config.write_text(json.dumps({"bili_root": str(app)}), encoding="utf-8")

    for media in (None, "audio-original", "audio-mp3", "audio-m4a"):
        for audio_quality in (None, "auto", "320", "256", "192", "128"):
            msg = dict(base)
            if media:
                msg.update(media=media, metadata=True)
            if audio_quality is not None:
                msg["audioQuality"] = audio_quality
            normalized = (audio_quality or "auto") if media in ("audio-mp3", "audio-m4a") else "auto"
            accepted = call(exe, msg, env)
            duplicate = call(exe, msg, env)
            check(duplicate.get("jobId") == accepted.get("jobId"), "Bili identical job dedupe")
            state = wait(exe, accepted, env)
            check(state["state"] == "completed" and state["media"] == (media or "video"), "Bili media completion")
            check(state.get("audioQuality") == normalized, "Bili status has normalized audioQuality")
            check(state["title"] == "Bili 合成标题完成" and len(state["files"]) == 1, "driver title/files survive C# final write")
            check(state["platform"] == "bilibili" and state["url"] == base["url"], "Bili status platform/url")
            check(bool(state["warnings"]) == bool(media) and bool(accepted["warnings"]) == bool(media), "Bili metadata explicitly unsupported")
            args = json.loads((app / "invocation.json").read_text(encoding="utf-8"))
            check(args[args.index("--media") + 1] == (media or "video") and args[args.index("--quality") + 1] == "best", "driver media and legacy quality arguments")
            check("--audio-quality" in args and args[args.index("--audio-quality") + 1] == normalized, "driver receives normalized audioQuality")
            record = json.loads((native / "jobs" / (accepted["jobId"] + ".json")).read_text(encoding="utf-8"))
            check(record.get("audioQuality") == normalized, "Bili persists audioQuality")
            check(record["driverExtra"] == "preserved" and record["subtitles"] == "off" and record["subLangs"] == "zh.*,en", "driver extra fields preserved")
            check(set(state) == {"id", "state", "percent", "downloadDir", "error", "platform", "url", "title", "media", "audioQuality", "files", "warnings",
                                  "danmaku", "biliSubtitles", "fileCount"}, "Bili job-status whitelist")
            check(args[args.index("--danmaku") + 1] == "off" and args[args.index("--subtitles") + 1] == "off", "driver receives default extras off")
            check(state["danmaku"] == "off" and state["biliSubtitles"] == "off" and state["fileCount"] == 1, "Bili status default extras and fileCount")
            check(call(exe, {"action": "open-job-folder", "jobId": accepted["jobId"]}, env)["selected"], "Bili BRIDGE_TEST select")
            check(call(exe, {"action": "open-job-file", "jobId": accepted["jobId"], "fileIndex": 0}, env)["ok"], "Bili opens recorded media only")
            for index in (-1, "0", 20, True):
                check(not call(exe, {"action": "open-job-file", "jobId": accepted["jobId"], "fileIndex": index}, env)["ok"], "Bili rejects invalid media index")
    for number, expected in ((9999, "failed"), (9998, "completed"), (9997, "failed"), (9996, "failed")):
        accepted = call(exe, {**base, "url": "https://www.bilibili.com/video/av" + str(number), "media": "audio-mp3", "audioQuality": "256"}, env)
        state = wait(exe, accepted, env)
        check(state["state"] == expected and len(state["files"]) == 1 and state["title"] == "Bili 合成标题完成", "preserve driver fields on nonterminal/abnormal exit")
        check("synthetic-private-value" not in json.dumps(state), "Bili unknown driver error is not exposed")
        check(state.get("audioQuality") == "256", "Bili failure status preserves requested bitrate")
        if number == 9996:
            check("完整 FFmpeg" in state["error"] and "自动音质" in state["error"], "Bili public status retains safe actionable encoder error")

    for number, keyword in ((9995, "ffprobe"), (9994, "无损封装")):
        accepted = call(exe, {**base, "url": "https://www.bilibili.com/video/av" + str(number), "media": "audio-original", "audioQuality": "320"}, env)
        state = wait(exe, accepted, env)
        check(state["state"] == "failed" and state["audioQuality"] == "auto" and keyword in state["error"], "original actionable errors survive safe public status")

    job_id = uuid.uuid4().hex
    path = native / "jobs" / (job_id + ".json")
    seeded = {"id": job_id, "url": base["url"], "quality": "best", "part": "current", "downloadDir": str(downloads),
              "state": "queued", "percent": 0, "pid": 0, "login": False,
              "updated": 621355968000000000 + int(time.time() * 10000000)}
    path.write_text(json.dumps(seeded), encoding="utf-8")
    check(call(exe, base, env).get("jobId") == job_id, "legacy Bili defaults dedupe")
    for stored in (None, "128", "320"):
        video_job = dict(seeded)
        if stored is not None:
            video_job["audioQuality"] = stored
        path.write_text(json.dumps(video_job), encoding="utf-8")
        for selected in ("auto", "320", "256", "192", "128"):
            check(call(exe, {**base, "audioQuality": selected}, env).get("jobId") == job_id, "video bitrates normalize before dedup")
    original_id = uuid.uuid4().hex
    original = jobs / (original_id + ".json")
    original.write_text(json.dumps({**seeded, "id": original_id, "media": "audio-original"}), encoding="utf-8")
    for selected in (None, "auto", "320", "256", "192", "128"):
        msg = {**base, "media": "audio-original"}
        if selected is not None:
            msg["audioQuality"] = selected
        check(call(exe, msg, env).get("jobId") == original_id, "original quality normalizes before dedup and legacy missing equals auto")
    original.unlink()
    path.write_text(json.dumps(seeded), encoding="utf-8")
    for media in ("audio-mp3", "audio-m4a"):
        ids = {}
        for selected in ("auto", "320", "256", "192", "128"):
            audio_id = uuid.uuid4().hex
            audio_job = {**seeded, "id": audio_id, "media": media}
            if selected != "auto":
                audio_job["audioQuality"] = selected
            (jobs / (audio_id + ".json")).write_text(json.dumps(audio_job), encoding="utf-8")
            ids[selected] = audio_id
        before = set(jobs.iterdir())
        for selected in (None, "auto", "320", "256", "192", "128"):
            msg = {**base, "media": media}
            if selected is not None:
                msg["audioQuality"] = selected
            check(call(exe, msg, env).get("jobId") == ids[selected or "auto"], "audio dedup distinguishes bitrate and missing equals auto")
        legacy = jobs / (ids["auto"] + ".json")
        legacy_job = json.loads(legacy.read_text(encoding="utf-8"))
        legacy_job["audioQuality"] = "auto"
        legacy.write_text(json.dumps(legacy_job), encoding="utf-8")
        check(call(exe, {**base, "media": media}, env).get("jobId") == ids["auto"], "omitted request equals stored auto")
        check(set(jobs.iterdir()) == before, "dedup does not start another bitrate worker")
        for audio_id in ids.values():
            (jobs / (audio_id + ".json")).unlink()
    distinct = [call(exe, {**base, **opts}, env) for opts in ({"media": "audio-mp3"}, {"subLangs": "en"}, {"metadata": True},
                {"quality": "80"}, {"part": "all"}, {"dir": str(alternate)}, {"danmaku": "xml"}, {"danmaku": "ass"},
                {"biliSubtitles": "srt"}, {"part": "collection"})]
    check(all(a.get("jobId") != job_id for a in distinct), "Bili dedupe includes media/langs/metadata/quality/part/dir")
    for accepted in distinct:
        check(wait(exe, accepted, env)["state"] == "completed", "distinct Bili option job completes")
    seeded.update(state="completed", files=[], error="synthetic-private-value", proxy="synthetic-private-value")
    files = []
    for index in range(23):
        p = downloads / ("finished-%d.mp4" % index)
        p.write_bytes(b"complete")
        files.append(str(p))
    seeded["files"] = [str(app / "outside.mp4"), str(downloads / "missing.mp4"), str(downloads), *files, files[0]]
    seeded["url"] += "?private=synthetic-private-value"
    seeded["percent"] = {"private": "synthetic-private-value"}
    seeded["audioQuality"] = "synthetic-private-value"
    seeded["media"] = "audio-mp3"
    path.write_text(json.dumps(seeded), encoding="utf-8")
    state = call(exe, {"action": "job-status", "jobId": job_id}, env)["job"]
    check(len(state["files"]) == 20 and all(Path(p).parent == downloads for p in state["files"]), "Bili max20 verified output paths")
    check("synthetic-private-value" not in json.dumps(state), "Bili status credential-safe")
    check(not call(exe, {"action": "open-job-folder", "jobId": job_id, "path": str(downloads)}, env)["ok"], "Bili only accepts jobId")
    check(not call(exe, {"action": "open-job-folder", "jobId": "../bad"}, env)["ok"], "Bili invalid jobId")
    for folder in ("relative", "D:drive-relative", "\\\\server\\share", "\\\\?\\C:\\Windows", str(downloads / "missing"), files[0], str(downloads) + "\t"):
        seeded["downloadDir"] = folder
        path.write_text(json.dumps(seeded), encoding="utf-8")
        check(not call(exe, {"action": "open-job-folder", "jobId": job_id}, env)["ok"], "Bili unsafe folder rejected")
    junction = fixture / "directory-junction"
    made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(alternate)], capture_output=True, timeout=10)
    check(made.returncode == 0, "create isolated Bili junction")
    try:
        seeded["downloadDir"] = str(junction)
        path.write_text(json.dumps(seeded), encoding="utf-8")
        check(not call(exe, {"action": "open-job-folder", "jobId": job_id}, env)["ok"], "Bili rejects reparse point directory")
    finally:
        os.rmdir(junction)
    seeded.update(downloadDir=str(downloads), files=[])
    path.write_text(json.dumps(seeded), encoding="utf-8")
    check(call(exe, {"action": "open-job-folder", "jobId": job_id}, env)["selected"] is False, "Bili folder fallback")
    seeded.update(state="downloading", updated=621355968000000000 + int((time.time() - 120) * 10000000))
    path.write_text(json.dumps(seeded), encoding="utf-8")
    check(call(exe, {"action": "job-status", "jobId": job_id}, env)["job"]["state"] == "failed", "Bili stale task")
    native_feature_tests(fixture, exe, isolated_release, env, native, app, downloads, cfg)
    native_terminal_worker_tests(exe, env, native, app, downloads)
    native_cancel_count_tests(exe, env, native, downloads)
    native_worker_timeout_test(fixture, source, env, app)
    return release


FEATURES = ["cancel", "danmaku", "subtitles", "collection", "pick-folder-initial", "login-status", "open-file"]
CANCELED = "已取消下载。"
WARN_DANMAKU = "弹幕下载失败，视频已保存。"
WARN_SUBTITLE = "字幕下载失败，视频已保存。"
WARN_FALLBACK = "该视频不属于合集，已按全部分 P 下载。"
WARN_PARTIAL = "部分条目下载失败。"


def process_alive(pid):
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def ticks(offset=0.0):
    return 621355968000000000 + int((time.time() + offset) * 10000000)


def native_feature_tests(fixture, exe, release, env, native, app, downloads, cfg):
    jobs = native / "jobs"
    base = {"action": "bili-download", "url": "https://www.bilibili.com/video/BV1GJ411x7h7"}

    # Section 1/10: features and read-only login state; ping never throws.
    ping = call(exe, {"action": "ping"}, env)
    check(ping.get("ok") is True and ping.get("features") == FEATURES, "Bili ping exact ordered features")
    check(ping.get("login") == {"bilibili": "guest"}, "Bili login guest from isolated flag")
    release_ping = call(release, {"action": "ping"}, env)
    check(release_ping.get("ok") is True and release_ping.get("login") == {"bilibili": "guest"}, "isolated release reads only the synthetic login flag")
    check(release_ping.get("ok") is True and release_ping.get("features") == FEATURES
          and list(release_ping.get("login", {})) == ["bilibili"]
          and release_ping["login"]["bilibili"] in ("signed-in", "guest", "unknown"), "release ping features/login shape")
    saved_cfg = cfg.read_bytes()
    try:
        for content, expected in ((json.dumps({"Cookie": {"is_login": True}}), "signed-in"),
                                  (json.dumps({"Download": {"download_path": str(downloads)},
                                               "Cookie": {"is_login": "true", "SESSDATA": "synthetic-private-value"}}), "signed-in"),
                                  (json.dumps({"Cookie": {"is_login": False, "SESSDATA": "synthetic-private-value"}}), "guest"),
                                  (json.dumps({"Download": {}}), "guest"),
                                  (json.dumps({"Cookie": "synthetic-private-value"}), "guest"),
                                  ("{not json", "unknown"), ("[]", "unknown"), ("", "unknown")):
            cfg.write_text(content, encoding="utf-8")
            response = call(exe, {"action": "ping"}, env)
            check(response.get("ok") is True and response.get("login") == {"bilibili": expected}
                  and response.get("features") == FEATURES, "login state derives from the flag only")
            check("synthetic-private-value" not in json.dumps(response, ensure_ascii=False), "ping never exposes cookie data")
        response = call(exe, {"action": "ping"}, dict(env, BILI_TEST_CONFIG=str(fixture / "missing-config.json")))
        check(response.get("ok") is True and response.get("login") == {"bilibili": "unknown"}, "missing config is unknown")
        response = call(exe, {"action": "ping"}, {k: v for k, v in env.items() if k != "BILI_TEST_CONFIG"})
        check(response.get("ok") is True and response.get("login") == {"bilibili": "unknown"}, "absent config source is unknown")
    finally:
        cfg.write_bytes(saved_cfg)

    # Section 6: pick-folder initialDir validation only (BRIDGE_TEST never opens a dialog).
    def pick(extra):
        return call(exe, {"action": "pick-folder", **extra}, env)
    check(pick({}) == {"ok": True, "canceled": True, "initialDir": ""}, "pick-folder without initialDir")
    check(pick({"initialDir": str(downloads)}) == {"ok": True, "canceled": True, "initialDir": str(downloads)}, "valid initialDir is used")
    some_file = downloads / "initial-file.txt"
    some_file.write_text("x", encoding="utf-8")
    junction = fixture / "initial-junction"
    made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(downloads)], capture_output=True, timeout=10)
    check(made.returncode == 0, "create initialDir junction")
    try:
        for bad in ("relative", "D:drive-relative", "\\\\server\\share", "\\\\?\\" + str(downloads), str(downloads / "missing"),
                    str(some_file), str(downloads) + "\t", "", " ", "C:\\" + "a" * 300, str(junction), str(junction / "x"),
                    "C:\\bad<name>"):
            check(pick({"initialDir": bad}) == {"ok": True, "canceled": True, "initialDir": ""}, "invalid initialDir silently falls back")
    finally:
        os.rmdir(junction)
        some_file.unlink()
    for bad in ({"initialDir": None}, {"initialDir": 1}, {"initialDir": [str(downloads)]}, {"dir": str(downloads)},
                {"initialDir": str(downloads), "title": "x"}):
        response = pick(bad)
        check(response.get("ok") is False and "initialDir" in response.get("error", ""), "pick-folder accepts only a string initialDir")

    # Section 5/8: strict new request fields, rejected before any job is created.
    before = set(jobs.iterdir())
    invalid = [{"danmaku": v} for v in (None, 1, True, "", "ASS", "json", "ass ", ["ass"], "off\n")]
    invalid += [{"biliSubtitles": v} for v in (None, 0, False, "", "SRT", "vtt", "ass", "srt --x", {"a": 1})]
    invalid += [{"part": v} for v in (None, 1, "", "Collection", "collection ", "season", ["all"])]
    for changed in invalid:
        response = call(exe, {**base, **changed}, env)
        check(response.get("ok") is False, "Bili new field strictly validated")
        key = next(iter(changed))
        if key != "part":
            check(key in response.get("error", ""), "strict extras error names the field")
    check(set(jobs.iterdir()) == before, "invalid new fields never create a job")

    extras_dir = fixture / "extras 目录"
    extras_dir.mkdir()
    accepted = call(exe, {**base, "danmaku": "ass", "biliSubtitles": "srt", "part": "collection", "dir": str(extras_dir)}, env)
    state = wait(exe, accepted, env)
    args = json.loads((app / "invocation.json").read_text(encoding="utf-8"))
    check(args[args.index("--danmaku") + 1] == "ass" and args[args.index("--subtitles") + 1] == "srt"
          and args[args.index("--part") + 1] == "collection", "driver receives danmaku/subtitles/collection")
    check(state["state"] == "completed" and state["danmaku"] == "ass" and state["biliSubtitles"] == "srt"
          and state["fileCount"] == 1, "job-status returns extras options")
    record = json.loads((jobs / (accepted["jobId"] + ".json")).read_text(encoding="utf-8"))
    check(record["danmaku"] == "ass" and record["biliSubtitles"] == "srt" and record["part"] == "collection", "extras options persisted")
    check(call(exe, {**base, "danmaku": "ass", "biliSubtitles": "srt", "part": "collection", "dir": str(extras_dir)}, env)["jobId"]
          != accepted["jobId"], "completed job is not reused")

    # job-status sanitizes warnings, fileCount and stored extras.
    sid = uuid.uuid4().hex
    done_file = extras_dir / "status.mp4"
    done_file.write_bytes(b"complete")
    seeded = {"id": sid, "url": base["url"], "quality": "best", "part": "collection", "downloadDir": str(extras_dir),
              "state": "completed", "percent": 100, "pid": 0, "login": False, "updated": ticks(), "files": [str(done_file)],
              "warnings": [WARN_DANMAKU, "synthetic-private-value", 5, WARN_SUBTITLE, WARN_DANMAKU, WARN_FALLBACK, WARN_PARTIAL],
              "fileCount": 57, "danmaku": "synthetic-private-value", "biliSubtitles": "srt"}
    (jobs / (sid + ".json")).write_text(json.dumps(seeded), encoding="utf-8")
    state = call(exe, {"action": "job-status", "jobId": sid}, env)["job"]
    check(state["warnings"] == [WARN_DANMAKU, WARN_SUBTITLE, WARN_FALLBACK, WARN_PARTIAL], "only whitelisted unique warnings")
    check(state["fileCount"] == 57 and state["danmaku"] == "off" and state["biliSubtitles"] == "srt", "fileCount and stored extras sanitized")
    check("synthetic-private-value" not in json.dumps(state, ensure_ascii=False), "status never leaks unknown values")
    for bad_count in ("57", -3, 0, None, 10 ** 12):
        seeded["fileCount"] = bad_count
        (jobs / (sid + ".json")).write_text(json.dumps(seeded), encoding="utf-8")
        count = call(exe, {"action": "job-status", "jobId": sid}, env)["job"]["fileCount"]
        check(isinstance(count, int) and 1 <= count <= 100000, "fileCount falls back to a sane integer")

    # Section 2: cancel request validation and idempotency.
    for message in ({"action": "cancel-job"}, {"action": "cancel-job", "jobId": "../x"}, {"action": "cancel-job", "jobId": 5},
                    {"action": "cancel-job", "jobId": "A" * 32}, {"action": "cancel-job", "jobId": sid, "force": True},
                    {"action": "cancel-job", "jobId": sid, "path": str(extras_dir)}):
        check(call(exe, message, env).get("ok") is False, "cancel-job strict keys/id")
    check(call(exe, {"action": "cancel-job", "jobId": uuid.uuid4().hex}, env) == {"ok": False, "error": "找不到此下载任务。"}, "cancel unknown job")
    for terminal in ("completed", "failed", "canceled"):
        seeded.update(state=terminal, fileCount=1)
        (jobs / (sid + ".json")).write_text(json.dumps(seeded), encoding="utf-8")
        before_bytes = (jobs / (sid + ".json")).read_bytes()
        check(call(exe, {"action": "cancel-job", "jobId": sid}, env) == {"ok": True, "jobId": sid, "state": terminal}, "terminal cancel is idempotent")
        check(not (jobs / (sid + ".cancel")).exists() and (jobs / (sid + ".json")).read_bytes() == before_bytes, "terminal cancel has no side effects")

    queued_dir = fixture / "queued 目录"
    queued_dir.mkdir()
    def seed(state, **extra):
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "url": base["url"], "quality": "best", "part": "current", "downloadDir": str(queued_dir),
               "state": state, "percent": 0, "pid": 0, "login": False, "updated": ticks(), "files": [], **extra}
        (jobs / (job_id + ".json")).write_text(json.dumps(job), encoding="utf-8")
        return job_id

    # Queued cancel: finalized without starting the engine.
    qid = seed("queued")
    check(call(exe, {"action": "cancel-job", "jobId": qid}, env) == {"ok": True, "jobId": qid, "state": "canceling"}, "queued cancel returns canceling")
    check((jobs / (qid + ".cancel")).is_file() and (jobs / (qid + ".cancel")).stat().st_size == 0, "cancel marker is an empty file")
    check(call(exe, {**base, "dir": str(queued_dir)}, env)["jobId"] != qid, "dedup ignores a job being canceled")
    state = call(exe, {"action": "job-status", "jobId": qid}, env)["job"]
    check(state["state"] == "canceled" and state["error"] == CANCELED and state["percent"] == 0, "queued job with dead worker finalizes canceled")
    check(not (jobs / (qid + ".cancel")).exists(), "marker removed after finalizing")
    check(call(exe, {"action": "cancel-job", "jobId": qid}, env) == {"ok": True, "jobId": qid, "state": "canceled"}, "repeat cancel idempotent")
    # Wait for the job started by the dedup proof above.
    for path in jobs.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("downloadDir") == str(queued_dir) and data.get("id") not in (qid,) and data.get("state") in ("queued", "downloading"):
            wait(exe, {"ok": True, "jobId": data["id"]}, env)
    cid = seed("canceled", error=CANCELED)
    check(call(exe, {**base, "dir": str(queued_dir)}, env)["jobId"] != cid, "dedup ignores canceled jobs")
    for path in jobs.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("downloadDir") == str(queued_dir) and data.get("state") in ("queued", "downloading"):
            wait(exe, {"ok": True, "jobId": data["id"]}, env)

    # Worker started for a queued job whose marker already exists: no engine.
    wid = seed("queued")
    (jobs / (wid + ".cancel")).write_bytes(b"")
    (app / "invocation.json").unlink()
    result = subprocess.run([str(exe), "--worker", wid], env=env, capture_output=True, timeout=30)
    record = json.loads((jobs / (wid + ".json")).read_text(encoding="utf-8"))
    check(result.returncode == 0 and record["state"] == "canceled" and record["error"] == CANCELED, "worker finalizes queued cancel")
    check(not (app / "invocation.json").exists() and not (jobs / (wid + ".cancel")).exists(), "queued cancel never starts the driver")

    # job-status with marker and dead worker while downloading: canceled, never the stale failure.
    for age in (-120, 0):
        did = seed("downloading", percent=42.5, updated=ticks(age))
        (jobs / (did + ".cancel")).write_bytes(b"")
        state = call(exe, {"action": "job-status", "jobId": did}, env)["job"]
        check(state["state"] == "canceled" and state["error"] == CANCELED and state["percent"] == 42.5, "dead worker with marker finalizes canceled")
        check(not (jobs / (did + ".cancel")).exists(), "dead worker marker removed")

    # Live cancel: the driver and its child are killed, private artefacts removed, others kept.
    cancel_dir = fixture / "cancel 目录"
    cancel_dir.mkdir()
    other = uuid.uuid4().hex
    keep = [cancel_dir / "keep.mp4", cancel_dir / (".veylo-partial-" + other + "-keep.mp4"),
            cancel_dir / (".veylo-original-" + other + "-keep") / "a.m4a", jobs / (other + ".work") / "keep.bin",
            cancel_dir / "合集 子目录" / ".veylo-partial-unrelated.mp4", cancel_dir / ".veylo-original-tmp-legacy" / "b.m4a"]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
    request = {**base, "url": "https://www.bilibili.com/video/av9990", "dir": str(cancel_dir)}
    accepted = call(exe, request, env)
    jid = accepted["jobId"]
    pids = []
    try:
        deadline = time.monotonic() + 20
        while not (app / ("cancel-ready-" + jid)).exists() and time.monotonic() < deadline:
            time.sleep(.05)
        check((app / ("cancel-ready-" + jid)).exists(), "long-running fake driver is ready")
        pids = [int((app / ("cancel-driver-" + jid + ".pid")).read_text()), int((app / ("cancel-child-" + jid + ".pid")).read_text())]
        check(all(process_alive(pid) for pid in pids), "fake driver and its child are running")
        work = jobs / (jid + ".work")
        private = [work, cancel_dir / (".veylo-original-" + jid + "-tmp1"), cancel_dir / "合集 子目录" / (".veylo-partial-" + jid + "-ep2.mp4")]
        check(all(p.exists() for p in private), "private artefacts exist before cancel")
        check(call(exe, request, env)["jobId"] == jid, "active job still deduplicated before cancel")
        started = time.monotonic()
        check(call(exe, {"action": "cancel-job", "jobId": jid}, env) == {"ok": True, "jobId": jid, "state": "canceling"}, "live cancel returns canceling")
        state = None
        while time.monotonic() - started < 15:
            state = call(exe, {"action": "job-status", "jobId": jid}, env)["job"]
            if state["state"] == "canceled":
                break
            check(state["state"] == "downloading", "canceling job remains active until finalized")
            time.sleep(.1)
        elapsed = time.monotonic() - started
        print("CANCEL finalized in %.2fs" % elapsed)
        check(state["state"] == "canceled" and elapsed < 6, "live cancel finalizes promptly")
        check(state["error"] == CANCELED and state["percent"] == 37.5, "canceled keeps percent and safe error")
        done = cancel_dir / "合集 子目录" / "ep1 完成.mp4"
        check(state["files"] == [str(done)] and done.read_bytes() == b"complete" and state["fileCount"] == 1, "completed outputs kept")
        check(not any(process_alive(pid) for pid in pids), "driver process tree killed")
        check(not any(p.exists() for p in private), "job work dir and tagged temp artefacts removed")
        check(all(p.read_bytes() == b"keep" for p in keep), "unrelated files and other jobs' artefacts survive")
        check(not (jobs / (jid + ".cancel")).exists(), "live marker removed")
        time.sleep(1.0)
        check(call(exe, {"action": "job-status", "jobId": jid}, env)["job"]["state"] == "canceled", "canceled state is never overwritten")
        check(call(exe, {"action": "cancel-job", "jobId": jid}, env) == {"ok": True, "jobId": jid, "state": "canceled"}, "live cancel idempotent")
        check(call(exe, {"action": "open-job-folder", "jobId": jid}, env)["selected"] is True, "canceled job folder opens")
    finally:
        for pid in pids:
            if process_alive(pid):
                subprocess.run([os.path.join(os.environ["SystemRoot"], "System32", "taskkill.exe"), "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)


def native_terminal_worker_tests(exe, env, native, app, downloads):
    jobs = native / "jobs"
    invocation = app / "invocation.json"
    for terminal in ("canceled", "completed", "failed"):
        jid = uuid.uuid4().hex
        record = jobs / (jid + ".json")
        job = {"id": jid, "url": "https://www.bilibili.com/video/av9989", "quality": "best", "part": "current",
               "downloadDir": str(downloads), "state": "queued" if terminal == "canceled" else terminal,
               "percent": 37.5, "pid": 0, "login": False, "updated": ticks(), "files": []}
        record.write_text(json.dumps(job), encoding="utf-8")
        if terminal == "canceled":
            check(call(exe, {"action": "cancel-job", "jobId": jid}, env)["state"] == "canceling", "delayed worker queued cancel accepted")
            state = call(exe, {"action": "job-status", "jobId": jid}, env)["job"]
            check(state["state"] == "canceled" and state["error"] == CANCELED
                  and not (jobs / (jid + ".cancel")).exists(), "status finalizes cancellation before delayed worker starts")
        before = record.read_bytes()
        invoked = invocation.read_bytes() if invocation.exists() else None
        result = subprocess.run([str(exe), "--worker", jid], env=env, capture_output=True, timeout=15)
        check(result.returncode == 0 and not result.stdout and not result.stderr, "delayed terminal worker exits cleanly")
        check(record.read_bytes() == before, "delayed worker never resurrects or rewrites " + terminal + " job")
        check((invocation.read_bytes() if invocation.exists() else None) == invoked, "terminal worker never starts engine")
    print("TERMINAL_WORKER_SUITE states=3 delayed canceled/completed/failed unchanged")


def native_cancel_count_tests(exe, env, native, downloads):
    jobs = native / "jobs"
    outputs = [downloads / ("cancel-count-%02d.mp4" % index) for index in range(27)]
    for path in outputs:
        path.write_bytes(b"complete")
    public = [str(path) for path in outputs[:20]]
    for stored, expected in ((27, 27), ("missing", 20), (None, 20), ("27", 20), (-1, 20), (100001, 20), (True, 20)):
        jid = uuid.uuid4().hex
        job = {"id": jid, "url": "https://www.bilibili.com/video/av9988", "quality": "best", "part": "collection",
               "downloadDir": str(downloads), "state": "downloading", "percent": 67.5, "pid": 0,
               "login": False, "updated": ticks(), "files": public}
        if stored != "missing":
            job["fileCount"] = stored
        else:
            job["files"] = [str(downloads / "missing.mp4"), str(native / "BiliBridge.exe"), *public]
        record = jobs / (jid + ".json")
        record.write_text(json.dumps(job), encoding="utf-8")
        state = call(exe, {"action": "job-status", "jobId": jid}, env)["job"]
        check(state["state"] == "downloading" and state["fileCount"] == expected, "pre-cancel status has maintained total or verified legacy fallback")
        check(call(exe, {"action": "cancel-job", "jobId": jid}, env)["state"] == "canceling", "count fixture requests cooperative cancel")
        state = call(exe, {"action": "job-status", "jobId": jid}, env)["job"]
        check(state["state"] == "canceled" and state["error"] == CANCELED and state["percent"] == 67.5, "dead-worker cancel preserves progress and error")
        check(state["fileCount"] == expected, "dead-worker cancel preserves total beyond the public 20-file cap")
        check(state["files"] == public and all(Path(path).is_file() for path in state["files"]), "canceled public files remain capped and existing inside output")
        check(all(path.read_bytes() == b"complete" for path in outputs), "cancel preserves all 27 completed files")
        check(not (jobs / (jid + ".cancel")).exists(), "count fixture removes its cancel marker")
    print("CANCEL_COUNT_SUITE completed=27 public=20 maintained-total/legacy-fallback/existing-files PASS")


def native_worker_timeout_test(fixture, source, env, app):
    native = fixture / "timeout-native"
    native.mkdir()
    jobs = native / "jobs"
    jobs.mkdir()
    (native / "bili_paths.json").write_text(json.dumps({"bili_root": str(app)}), encoding="utf-8")
    source_text = source.read_text(encoding="utf-8")
    scaled = fixture / "BiliBridgeTimeout.cs"
    # Scale only a disposable BRIDGE_TEST source, never the formal release or an environment setting.
    scaled.write_text(source_text.replace("7200000", "1500"), encoding="utf-8")
    exe = native / "BiliBridge.exe"
    compile_cs(scaled, exe, True)
    downloads = fixture / "timeout 输出"
    downloads.mkdir()
    jid, other = uuid.uuid4().hex, uuid.uuid4().hex
    record = jobs / (jid + ".json")
    record.write_text(json.dumps({"id": jid, "url": "https://www.bilibili.com/video/av9990", "quality": "best", "part": "current",
                                 "downloadDir": str(downloads), "state": "queued", "percent": 0, "pid": 0,
                                 "login": False, "updated": ticks(), "files": []}), encoding="utf-8")
    keep = [downloads / "keep.mp4", downloads / (".veylo-partial-" + other + "-keep.mp4"),
            downloads / (".veylo-original-" + other + "-keep") / "a.m4a", jobs / (other + ".work") / "keep.bin"]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
    sentinel = subprocess.Popen([str(app / "runtime/python.exe"), "--child", str(fixture / "timeout-sentinel.pid")],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    worker, pids = None, []
    started = time.monotonic()
    try:
        worker = subprocess.Popen([str(exe), "--worker", jid], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready = app / ("cancel-ready-" + jid)
        while not ready.exists() and worker.poll() is None and time.monotonic() - started < 6:
            time.sleep(.025)
        check(ready.exists(), "timeout fixture starts its owned driver and child before deadline")
        pids = [int((app / ("cancel-driver-" + jid + ".pid")).read_text()), int((app / ("cancel-child-" + jid + ".pid")).read_text())]
        print("TIMEOUT fixture ready driver_pid=%d child_pid=%d" % tuple(pids))
        while worker.poll() is None and time.monotonic() - started < 6:
            time.sleep(.025)
        elapsed = time.monotonic() - started
        check(worker.poll() is not None and worker.returncode == 0, "two-hour deadline stops the worker in the short scaled fixture")
        check(source_text.count("7200000") == 1, "timeout fixture scales exactly one production two-hour deadline literal")
        state = call(exe, {"action": "job-status", "jobId": jid}, env)["job"]
        check(state["state"] == "failed" and state["error"] == "下载超过两小时，已停止。", "timeout retains the original failure and Chinese error, not canceled")
        check(state["percent"] == 37.5, "timeout keeps driver progress")
        done = downloads / "合集 子目录" / "ep1 完成.mp4"
        check(state["files"] == [str(done)] and state["fileCount"] == 1 and done.read_bytes() == b"complete", "timeout preserves completed media")
        private = [jobs / (jid + ".work"), downloads / (".veylo-original-" + jid + "-tmp1"),
                   downloads / "合集 子目录" / (".veylo-partial-" + jid + "-ep2.mp4")]
        check(not any(process_alive(pid) for pid in pids), "timeout kills only the owned engine PID tree")
        check(sentinel.poll() is None, "timeout leaves a separately owned same-image sentinel alive")
        check(not any(path.exists() for path in private), "timeout removes this job's work and tagged staging only")
        check(all(path.read_bytes() == b"keep" for path in keep), "timeout preserves other jobs' artifacts and existing media")
        check(not (jobs / (jid + ".cancel")).exists(), "timeout never creates a user cancel marker")
        print("TIMEOUT_SUITE scaled_limit_ms=1500 elapsed=%.2fs failed/tree-kill/cleanup/preserved-media PASS" % elapsed)
    finally:
        owned = ([worker.pid] if worker is not None else []) + pids + [sentinel.pid]
        for pid in owned:
            if process_alive(pid):
                subprocess.run([os.path.join(os.environ["SystemRoot"], "System32", "taskkill.exe"), "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)
        if worker is not None:
            worker.wait(timeout=15)
        sentinel.wait(timeout=15)


def driver_feature_tests(driver, module_map, fixture, bili_root, media_root, source_audio):
    from test_driver_veylo_features import run_suite
    result = run_suite()
    check(result.wasSuccessful() and result.testsRun == 40 and not result.skipped,
          "all 40 offline driver feature cases must pass without skips")
    print("DRIVER_FEATURE_SUITE tests=%d failures=%d errors=%d skipped=%d" %
          (result.testsRun, len(result.failures), len(result.errors), len(result.skipped)))


def driver_tests(fixture):
    driver = load_driver(fixture)
    module_map = modules_for_driver()
    media_root = fixture / "media"
    media_root.mkdir()
    work = media_root / "work"
    work.mkdir()
    out = media_root / "out"
    out.mkdir()
    # Locate installed test tools by bounded paths; never read configuration values.
    category = Path.home() / "Desktop" / "Applications"
    roots = ([Path(os.environ["VEYLO_TEST_BILI_DIR"])] if os.environ.get("VEYLO_TEST_BILI_DIR") else
             [p for p in category.glob("Bili23-Downloader_*_windows_x64_portable/Bili23-Downloader")
              if (p / "bundle/ffmpeg.exe").is_file()])
    check(len(roots) == 1, "one installed Bili23 test tool location")
    bili_root = roots[0]
    ffmpeg = bili_root / "bundle/ffmpeg.exe"
    ffprobe = (Path(os.environ["VEYLO_TEST_FLUENT_DIR"]) if os.environ.get("VEYLO_TEST_FLUENT_DIR") else Path.home() / "Desktop" / "FluentYTDL-3.7.6-win64-full") / "bin/ffmpeg/ffprobe.exe"
    synth_ffmpeg = ffprobe.with_name("ffmpeg.exe")
    fixture_tool_config = fixture / "fluent_paths.json"
    fixture_tool_config.write_text(json.dumps({"fluent_root": str(ffprobe.parents[2])}), encoding="utf-8")
    driver.FLUENT_PATHS = fixture_tool_config
    check(ffmpeg.is_file() and ffprobe.is_file() and synth_ffmpeg.is_file(), "existing FFmpeg/ffprobe available")

    def command(args):
        proc = subprocess.run([str(a) for a in args], capture_output=True, timeout=30)
        if proc.returncode:
            raise AssertionError("synthetic media command failed: " + proc.stderr.decode("utf-8", "replace")[-1500:])
        return proc

    def probe(path):
        return json.loads(command([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path]).stdout)

    source_audio = work / "source.m4a"
    source_video = work / "source.mp4"
    muxed = work / "muxed.mp4"
    command([synth_ffmpeg, "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=700:duration=0.25", "-c:a", "aac", source_audio])
    command([synth_ffmpeg, "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=0.3", "-c:v", "libx264", source_video])
    command([synth_ffmpeg, "-nostdin", "-y", "-i", source_video, "-i", source_audio, "-c", "copy", muxed])
    check(source_audio.stat().st_size > 0 and muxed.stat().st_size > 0, "short synthetic media generated")

    with patch.dict(sys.modules, module_map):
        task = driver.make_task({"title": "part", "bvid": "BV1GJ411x7h7", "cid": 1}, "parent")
        check(task.Download.type == DownloadType.VIDEO | DownloadType.AUDIO, "legacy DownloadType flags")
        for media in ("audio-mp3", "audio-m4a"):
            audio_task = driver.make_task({"title": "part"}, "parent", media)
            check(audio_task.Download.type == DownloadType.AUDIO, "audio DownloadType.AUDIO only")
        argv = ["--bili-root", "dummy", "--url", "dummy", "--job-id", "a" * 32, "--jobs-dir", "dummy", "--output-dir", "dummy"]
        defaults = driver.build_argparser().parse_args(argv)
        check(defaults.media == "video", "driver default media")
        check(getattr(defaults, "audio_quality", None) == "auto", "driver default audioQuality")
        for selected in ("auto", "320", "256", "192", "128"):
            parsed = driver.build_argparser().parse_args(argv + ["--audio-quality", selected])
            check(parsed.audio_quality == selected, "driver accepts exact audio quality enum")
        for changed in (["--media", "bad"], ["--quality", "bad"], ["--audio-format", "anything"],
                        *(["--audio-quality", invalid] for invalid in ("", "AUTO", "64", "320k", " 320", "320 ", "320 --exec bad"))):
            with redirect_stderr(io.StringIO()):
                try:
                    driver.build_argparser().parse_args(argv + changed)
                except SystemExit as exc:
                    check(exc.code != 0, "driver rejects arbitrary options")
                else:
                    raise AssertionError("driver accepted invalid arguments")

        invalid_jobs = media_root / "invalid-jobs"
        invalid_jobs.mkdir()
        for media in ("video", "audio-original", "audio-mp3", "audio-m4a"):
            for invalid in (None, 320, 128.0, True, False, [], {}, ["320"], "", "AUTO", "64", "320k", " 320", "320 ", "320\n", "320 --exec bad"):
                job_id = uuid.uuid4().hex
                record = invalid_jobs / (job_id + ".json")
                record.write_text(json.dumps({"id": job_id}), encoding="utf-8")
                args = SimpleNamespace(bili_root=str(bili_root), output_dir=str(out), jobs_dir=str(invalid_jobs),
                                       job_id=job_id, media=media, audio_quality=invalid, quality="best")
                before = record.read_bytes()
                with patch.object(driver, "setup_bili") as setup:
                    try:
                        driver.run(args, driver.Job(record))
                    except ValueError as exc:
                        check("audioQuality" in str(exc), "driver strict quality error names contract field")
                    else:
                        raise AssertionError("driver accepted malformed audioQuality")
                    check(not setup.called and not (invalid_jobs / (job_id + ".work")).exists()
                          and record.read_bytes() == before, "driver invalid audioQuality rejected before all work")

        for media in ("audio-mp3", "audio-m4a"):
            job_id = uuid.uuid4().hex
            record = invalid_jobs / (job_id + ".json")
            record.write_text(json.dumps({"id": job_id, "audioQuality": "320"}), encoding="utf-8")
            args = SimpleNamespace(bili_root=str(bili_root), output_dir=str(out), jobs_dir=str(invalid_jobs),
                                   job_id=job_id, media=media, audio_quality="320", quality="best", url="offline", part="current")
            before = record.read_bytes()
            with patch.object(driver, "audio_ffmpeg", return_value=(str(ffmpeg), False)), \
                 patch.object(driver, "setup_bili", side_effect=AssertionError("setup ran before encoder preflight")) as setup, \
                 patch.object(driver, "parse_url") as parse, patch.object(driver, "download_entry") as download:
                try:
                    driver.run(args, driver.Job(record))
                except driver.AudioEncoderUnavailable as exc:
                    check(driver.friendly(exc) == driver.AUDIO_ENCODER_ERROR, "missing encoder fails before setup with actionable error")
                else:
                    raise AssertionError("driver did not preflight explicit bitrate encoder")
                check(not setup.called and not parse.called and not download.called
                      and not (invalid_jobs / (job_id + ".work")).exists() and record.read_bytes() == before,
                      "missing encoder rejects explicit bitrate before setup/parse/download/work")

        check(driver.build_argparser().parse_args(argv + ["--media", "audio-original"]).media == "audio-original", "driver accepts original media contract")
        check(driver.make_task({}, "", "audio-original").Download.type == DownloadType.AUDIO, "original requests audio only")
        missing_tools = media_root / "missing-probe"
        missing_tools.mkdir()
        (missing_tools / "ffmpeg.exe").write_bytes(b"not executed")
        for tool, full in ((str(ffmpeg), False), (str(missing_tools / "ffmpeg.exe"), True)):
            job_id = uuid.uuid4().hex
            record = invalid_jobs / (job_id + ".json")
            record.write_text(json.dumps({"id": job_id, "media": "audio-original", "audioQuality": "auto"}), encoding="utf-8")
            args = SimpleNamespace(bili_root=str(bili_root), output_dir=str(out), jobs_dir=str(invalid_jobs),
                                   job_id=job_id, media="audio-original", audio_quality="320", quality="best", url="offline", part="current")
            before = record.read_bytes()
            with patch.object(driver, "audio_ffmpeg", return_value=(tool, full)), patch.object(driver, "setup_bili") as setup, \
                 patch.object(driver, "parse_url") as parse, patch.object(driver, "download_entry") as download:
                try:
                    driver.run(args, driver.Job(record))
                except RuntimeError as exc:
                    message = driver.friendly(exc)
                    check("完整 FFmpeg" in message and "ffprobe" in message and "MP3/M4A" in message, "original missing complete tools has actionable failure")
                else:
                    raise AssertionError("original accepted missing complete tools")
                check(not setup.called and not parse.called and not download.called
                      and not (invalid_jobs / (job_id + ".work")).exists() and record.read_bytes() == before,
                      "original requires complete tool pair before setup/parse/download/work")

        # A fake API parser proves audio-only DASH selection and muxed legacy fallback.
        context = {"type": MediaType.DASH, "video_calls": 0, "deny": False}

        class ParseWorker:
            def __init__(self, task):
                self.task = task
            def get_info(self):
                if context["deny"]:
                    raise RuntimeError("权限 denied synthetic-private-value")
                self.task.Download.media_type = context["type"]
                self.info_data = context.get("info") or {"authorized": True}

        class VideoParser:
            def __init__(self, info, task):
                check(info == {"authorized": True}, "only existing authorized playurl data")
                self.task = task
            def get_available_quality_list(self):
                return [16, 80]
            def parse_info(self):
                context["video_calls"] += 1
                key = "video_part_1" if context["type"] in (MediaType.MP4, MediaType.FLV) else "video"
                return [{"file_key": key, "file_name": "muxed.mp4", "file_size": 1}]

        class AudioParser:
            def __init__(self, info, task):
                self.task = task
            def parse_info(self):
                if context["type"] in (MediaType.MP4, MediaType.FLV):
                    self.task.Download.type &= ~DownloadType.AUDIO
                    return []
                return [{"file_key": "audio", "file_name": "source.m4a", "file_size": 1}]

        module_map["util.download.downloader.parse_worker"].ParseWorker = ParseWorker
        module_map["util.download.parse.video_info"].VideoInfoParser = VideoParser
        module_map["util.download.parse.audio_info"].AudioInfoParser = AudioParser
        entries, media_type = driver.resolve_entries(driver.make_task({}, "", "audio-mp3"), "best", "audio-mp3")
        check([e["file_key"] for e in entries] == ["audio"] and context["video_calls"] == 0, "DASH audio never fetches video")
        for mt in (MediaType.MP4, MediaType.FLV):
            context.update(type=mt, video_calls=0)
            entries, _ = driver.resolve_entries(driver.make_task({}, "", "audio-m4a"), "80", "audio-m4a")
            check(entries[0]["file_key"] == "video_part_1" and context["video_calls"] == 1, "legacy muxed audio fallback")
        context.update(type=MediaType.DASH, deny=True, video_calls=0)
        try:
            driver.resolve_entries(driver.make_task({}, "", "audio-mp3"), "best", "audio-mp3")
        except RuntimeError:
            check(context["video_calls"] == 0, "access errors propagate without fallback")
        else:
            raise AssertionError("authorization error ignored")
        context["deny"] = False
        class QueryWorker:
            def __init__(self, info):
                self.info = info
            def query_dash_url(self):
                return {"url": self.info["marker"], "file_size": 10}
        module_map["util.download.parse.query_worker"].QueryWorker = QueryWorker
        low = {"id": 30216, "codecs": "mp4a.40.2", "bandwidth": 64000, "marker": "low", "channels": 2}
        high = {"id": 30280, "codecs": "mp4a.40.2", "bandwidth": 192000, "marker": "high", "channels": 2}
        high_better = {**high, "bandwidth": 192500, "marker": "high-better"}
        dolby = {"id": 30250, "codecs": "ec-3", "bandwidth": 120000, "marker": "dolby", "channels": 6}
        flac = {"id": 30251, "codecs": "fLaC", "bandwidth": 100000, "marker": "flac", "channels": 2}
        for dash, expected in (({"audio": [low, high, high_better], "dolby": {"audio": [dolby]}, "flac": {"audio": flac}}, "flac"),
                               ({"audio": [low, high_better], "dolby": {"audio": [dolby]}}, "dolby"),
                               ({"audio": [{**low, "bandwidth": 999999}, high, high_better]}, "high-better"),
                               ({"audio": [high, {**high_better, "codecs": "opus", "bandwidth": 999999, "marker": "other-codec"}]}, "high"),
                               ({"audio": [high, {**high_better, "channels": 6, "bandwidth": 999999, "marker": "other-channels"}]}, "high")):
            context["info"] = {"dash": dash}
            original_task = driver.make_task({}, "", "audio-original")
            entries, _ = driver.resolve_entries(original_task, "best", "audio-original")
            check(entries[0].get("url") == expected, "original explicit FLAC/Dolby/service tier policy ignores configured priority")
            check(original_task.Download.audio_quality_id != 30300 and context["video_calls"] == 0, "original selects only offered audio without requesting new privileges/video")
        context.pop("info")

        # FFmpeg command contract: explicit rates encode, auto keeps its legacy flags.
        audio_entry = {"file_key": "audio", "file_name": source_audio.name}
        for media, codec, ext in (("audio-mp3", "libmp3lame", ".mp3"), ("audio-m4a", "aac", ".m4a")):
            for selected in (None, "auto", "320", "256", "192", "128"):
                kwargs = {} if selected is None else {"audio_quality": selected}
                with patch.object(driver, "audio_ffmpeg", return_value=(str(synth_ffmpeg), True)), \
                     patch.object(driver, "run_ffmpeg") as ff:
                    driver.merge(task, [audio_entry], MediaType.DASH, work, out / ("flags" + ext), bili_root, media, **kwargs)
                cmd = ff.call_args.args[0]
                check(cmd[cmd.index("-c:a") + 1] == codec and "copy" not in cmd, "audio format selects real encoder")
                check(cmd[cmd.index("-map") + 1] == "0:a:0" and "-vn" in cmd, "audio command maps no video")
                if selected in (None, "auto") and media == "audio-mp3":
                    check(cmd[cmd.index("-q:a") + 1] == "0" and "-b:a" not in cmd, "auto MP3 retains q0")
                else:
                    rate = "192" if selected in (None, "auto") else selected
                    check(cmd[cmd.index("-b:a") + 1] == rate + "k" and "-q:a" not in cmd, "explicit bitrate has no VBR quality conflict")
        with patch.object(driver, "audio_ffmpeg", return_value=(str(ffmpeg), False)), patch.object(driver, "run_ffmpeg") as ff:
            driver.merge(task, [audio_entry], MediaType.DASH, work, out / "auto-copy.m4a", bili_root, "audio-m4a")
        check(ff.call_args.args[0][ff.call_args.args[0].index("-c:a") + 1] == "copy", "auto M4A retains small-bundle copy fallback")

        for media, ext in (("audio-mp3", ".mp3"), ("audio-m4a", ".m4a")):
            for selected in ("320", "256", "192", "128"):
                output = out / ("unavailable-" + media + "-" + selected + ext)
                with patch.object(driver, "audio_ffmpeg", return_value=(str(ffmpeg), False)), \
                     patch.object(driver, "run_ffmpeg") as ff:
                    try:
                        driver.merge(task, [audio_entry], MediaType.DASH, work, output, bili_root, media, selected)
                    except RuntimeError as exc:
                        message = driver.friendly(exc)
                        check("FFmpeg" in message and "完整" in message and "自动" in message, "explicit bitrate missing encoder error is actionable")
                    else:
                        raise AssertionError("explicit bitrate silently used incomplete FFmpeg")
                    check(not ff.called and not output.exists(), "explicit bitrate never falls back to small-bundle copy/encoder")

        # Exercise the actual FFmpeg merge/extract commands on local synthetic data.
        audio_entry = {"file_key": "audio", "file_name": source_audio.name}
        video_entry = {"file_key": "video", "file_name": source_video.name}
        dash_video = out / "dash-video.mp4"
        driver.merge(task, [video_entry, audio_entry], MediaType.DASH, work, dash_video, bili_root)
        check({s["codec_type"] for s in probe(dash_video)["streams"]} == {"video", "audio"}, "legacy DASH video merge")
        for media in ("audio-mp3", "audio-m4a"):
            output = out / ("dash-" + media + "." + media[6:])
            driver.merge(task, [audio_entry], MediaType.DASH, work, output, bili_root, media)
            streams = probe(output)["streams"]
            check(len(streams) == 1 and streams[0]["codec_type"] == "audio", "DASH extraction has no video")
            check(streams[0]["codec_name"] == ("mp3" if media == "audio-mp3" else "aac"), "requested audio codec")
            legacy = out / ("legacy-" + media + "." + media[6:])
            driver.merge(task, [{"file_key": "video_part_1", "file_name": muxed.name}], MediaType.MP4, work, legacy, bili_root, media)
            check(len(probe(legacy)["streams"]) == 1, "legacy muxed extraction has no video")
        parts = [{"file_key": "video_part_10", "file_name": muxed.name}, {"file_key": "video_part_2", "file_name": muxed.name}]
        joined = out / "legacy-multipart.m4a"
        driver.merge(task, parts, MediaType.FLV, work, joined, bili_root, "audio-m4a")
        info = probe(joined)
        check(len(info["streams"]) == 1 and float(info["format"]["duration"]) >= .45, "legacy multipart audio concat")
        for media in ("audio-mp3", "audio-m4a"):
            for selected in ("auto", "320", "256", "192", "128"):
                recorded = []
                with patch.object(driver, "run_ffmpeg", side_effect=lambda cmd, cwd: recorded.append(cmd)):
                    driver.merge(task, [{"file_key": "video_part_10", "file_name": "ten.mp4"}, {"file_key": "video_part_2", "file_name": "two.mp4"}], MediaType.MP4, work, joined, bili_root, media, selected)
                cmd = recorded[0]
                check(cmd[cmd.index("-i") + 1].endswith("two.mp4") and any(arg.endswith("concat=n=2:v=0:a=1[outa]") for arg in cmd), "legacy parts sorted numerically")
                check(cmd[cmd.index("-map") + 1] == "[outa]" and "-vn" in cmd, "multipart uses audio-only concat output")
                if selected != "auto":
                    check(cmd[cmd.index("-b:a") + 1] == selected + "k" and "-q:a" not in cmd, "multipart retains requested bitrate")
        complex_audio = work / "complex-stereo.wav"
        command([synth_ffmpeg, "-nostdin", "-y", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.35:sample_rate=48000:duration=12:seed=123",
                 "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.2:sample_rate=48000:duration=12:seed=456",
                 "-filter_complex", "[0:a][1:a]amerge=inputs=2[a]", "-map", "[a]", "-c:a", "pcm_s16le", complex_audio])
        for media, codec, ext in (("audio-mp3", "mp3", ".mp3"), ("audio-m4a", "aac", ".m4a")):
            for selected in ("320", "256", "192", "128"):
                output = out / ("selected-" + selected + ext)
                driver.merge(task, [{"file_key": "audio", "file_name": complex_audio.name}], MediaType.DASH, work, output, bili_root, media, selected)
                info = probe(output)
                stream = info["streams"][0]
                check(len(info["streams"]) == 1 and stream["codec_type"] == "audio" and stream["codec_name"] == codec, "selected bitrate produces requested actual codec only")
                actual = int(stream["bit_rate"])
                check(float(info["format"]["duration"]) >= 11.9 and abs(actual - int(selected) * 1000) <= int(selected) * 1000 * .15,
                      "selected actual bitrate within 15 percent on 12-second complex stereo source")
                print("BITRATE", media, selected, "actual", actual, "duration", info["format"]["duration"])
        # Original mode is exercised through run(), not a mocked encoder/probe.
        original_out = media_root / "original-output"
        original_out.mkdir()
        def original_run(sources, media_type=MediaType.DASH, failure=False, collision=False):
            job_id = uuid.uuid4().hex
            jobs_dir = media_root / ("original-jobs-" + job_id)
            jobs_dir.mkdir()
            record = jobs_dir / (job_id + ".json")
            record.write_text(json.dumps({"id": job_id, "downloadDir": str(original_out)}), encoding="utf-8")
            entries = [{"file_key": key, "file_name": "source-%d%s" % (i, path.suffix), "file_size": path.stat().st_size}
                       for i, (key, path) in enumerate(sources)]
            source_map = {entry["file_name"]: source[1] for entry, source in zip(entries, sources)}
            args = SimpleNamespace(bili_root=str(bili_root), output_dir=str(original_out), jobs_dir=str(jobs_dir),
                                   job_id=job_id, media="audio-original", audio_quality="320", quality="best", url="offline", part="current")
            def download_original(entry, directory, job, progress):
                check(job.data.get("audioQuality") == "auto", "original normalizes bitrate before download")
                shutil.copy2(source_map[entry["file_name"]], directory / entry["file_name"])
                progress(entry["file_size"])
            with patch.object(driver, "setup_bili"), patch.object(driver, "parse_url", return_value=([{"title": "source", "part_number": 2}], "Original title")), \
                 patch.object(driver, "resolve_entries", return_value=(entries, media_type)), patch.object(driver, "download_entry", side_effect=download_original), \
                 patch.object(driver, "audio_ffmpeg", return_value=(str(synth_ffmpeg), True)), patch.object(driver, "run_ffmpeg", wraps=driver.run_ffmpeg) as ff:
                if failure:
                    before_outputs = set(original_out.iterdir())
                    if collision:
                        real_output_path_for = driver.output_path_for
                        def occupied_path(*a, **kw):
                            output = real_output_path_for(*a, **kw)
                            output.write_bytes(b"another completed job")
                            return output
                    try:
                        with patch.object(driver, "output_path_for", side_effect=occupied_path) if collision else patch.object(driver, "output_path_for", wraps=driver.output_path_for):
                            driver.run(args, driver.Job(record))
                    except driver.OriginalCopyError as exc:
                        check(driver.friendly(exc) == driver.ORIGINAL_COPY_ERROR, "incompatible original multipart reports safe actionable failure")
                    else:
                        raise AssertionError("incompatible original multipart accepted")
                    work_dir = jobs_dir / (job_id + ".work")
                    if collision:
                        added = set(original_out.iterdir()) - before_outputs
                        check(len(added) == 1 and next(iter(added)).read_bytes() == b"another completed job", "original collision never overwrites or deletes another job output")
                    else:
                        check(not ff.called and set(original_out.iterdir()) == before_outputs and not list(work_dir.glob("original-copy-*")),
                              "incompatible multipart never encodes or leaves output/private concat files")
                    return
                driver.run(args, driver.Job(record))
            result = json.loads(record.read_text(encoding="utf-8"))
            check(result["state"] == "completed" and result["media"] == "audio-original" and result["audioQuality"] == "auto", "original lifecycle preserves contract")
            check(result["title"] == "Original title" and len(result["files"]) == 1, "original lifecycle exposes actual file/title")
            output = Path(result["files"][0])
            check("(P2) [original]" in output.stem and not (jobs_dir / (job_id + ".work")).exists(), "original naming and own work cleanup")
            for call_args in ff.call_args_list:
                cmd = call_args.args[0]
                check(Path(cmd[-1]).parent.name.startswith(".veylo-original-" + job_id + "-"), "original temp dir is tagged with its job id for cancel cleanup")
                check(cmd[cmd.index("-c:a") + 1] == "copy" and not set(cmd).intersection({"-b:a", "-q:a", "-ar", "-ac", "-af", "-filter_complex"}), "original commands copy packets without bitrate/resample/remix/filter")
            return output
        def packet_hashes(path):
            data = json.loads(command([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_packets", "-show_data_hash", "sha256",
                                       "-show_entries", "packet=data_hash", "-of", "json", path]).stdout)
            return [p["data_hash"] for p in data["packets"]]
        def pcm_hash(path):
            return command([synth_ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0", "-c:a", "pcm_s32le", "-f", "hash", "-hash", "sha256", "-"]).stdout
        for codec, encoder, ext, source_ext in (("aac", "aac", ".m4a", ".m4a"), ("flac", "flac", ".flac", ".flac"),
                                               ("eac3", "eac3", ".eac3", ".eac3"), ("opus", "libopus", ".opus", ".opus"),
                                               ("alac", "alac", ".m4a", ".m4a"), ("mp3", "libmp3lame", ".mp3", ".mp3"),
                                               ("vorbis", "libvorbis", ".ogg", ".ogg"), ("ac3", "ac3", ".ac3", ".ac3"),
                                               ("pcm_s16le", "pcm_s16le", ".mka", ".mka")):
            source = work / ("original-" + codec + source_ext)
            command([synth_ffmpeg, "-nostdin", "-y", "-i", complex_audio, "-t", "2", "-c:a", encoder, source])
            copied = original_run([("audio", source)])
            before, after = probe(source)["streams"][0], probe(copied)["streams"][0]
            check(copied.suffix == ext and after["codec_name"] == codec, "original container follows actual source codec")
            check((before["sample_rate"], before["channels"], before.get("channel_layout"))
                  == (after["sample_rate"], after["channels"], after.get("channel_layout")), "original sample rate and channel layout unchanged")
            check(packet_hashes(source) == packet_hashes(copied), "original encoded packet hashes exactly preserved")
            check(pcm_hash(source) == pcm_hash(copied), "original decoded PCM unchanged")
            print("ORIGINAL_COPY", codec, before["sample_rate"], before["channels"], "packet+PCM identical")
        copied = original_run([("video_part_1", muxed)], MediaType.MP4)
        check(len(probe(copied)["streams"]) == 1 and packet_hashes(muxed) == packet_hashes(copied)
              and pcm_hash(muxed) == pcm_hash(copied), "original muxed fallback extracts unchanged audio only")

        existing_aac = work / "original-aac.m4a"
        first_copy = original_run([("audio", existing_aac)])
        saved_copy = first_copy.read_bytes()
        second_copy = original_run([("audio", existing_aac)])
        check(first_copy != second_copy and first_copy.read_bytes() == saved_copy, "original repeated output never overwrites earlier copy")
        for codec, ext in (("aac", ".m4a"), ("flac", ".flac"), ("eac3", ".eac3"), ("opus", ".opus")):
            source = work / ("original-" + codec + ext)
            parts = []
            for number, start in ((2, "0"), (10, "1")):
                part = work / ("order-%s-%d.mka" % (codec, number))
                command([synth_ffmpeg, "-nostdin", "-y", "-i", source, "-ss", start, "-t", "0.7", "-map", "0:a:0", "-c:a", "copy", part])
                parts.append(part)
            hashes = [packet_hashes(part) for part in parts]
            check(all(hashes) and hashes[0] != hashes[1], "multipart order fixtures contain distinct encoded payloads")
            signatures = [json.loads(command([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_streams", "-show_data_hash", "sha256", "-of", "json", part]).stdout)["streams"][0] for part in parts]
            keys = ("codec_name", "profile", "sample_fmt", "sample_rate", "channels", "channel_layout", "bits_per_raw_sample", "extradata_hash")
            check(tuple(signatures[0].get(key) for key in keys) == tuple(signatures[1].get(key) for key in keys), "multipart order fixtures have compatible codec/sample/channels/extradata")
            copied = original_run([("video_part_10", parts[1]), ("video_part_2", parts[0])], MediaType.FLV)
            check(packet_hashes(copied) == hashes[0] + hashes[1], "compatible multipart retains every encoded source packet in numeric part order")
            stream = probe(copied)["streams"][0]
            check(stream["codec_name"] == codec and stream["sample_rate"] == "48000" and stream["channels"] == 2, "multipart codec/sample/channels preserved")
            print("ORIGINAL_MULTIPART", codec, "all source packets identical")
        original_run([("video_part_2", work / "original-aac.m4a"), ("video_part_10", work / "original-opus.opus")], MediaType.FLV, failure=True)
        original_run([("audio", work / "original-aac.m4a")], failure=True, collision=True)

        old_copy = work / "old.mp4"
        shutil.copy2(muxed, old_copy)
        driver.merge(task, [{"file_key": "video_part_1", "file_name": old_copy.name}], MediaType.MP4, work, out / "legacy-video.mp4", bili_root)
        check(not old_copy.exists() and (out / "legacy-video.mp4").is_file(), "legacy single video move retained")

        class Formatter:
            rel = "custom/video.mp4"
            def set_variable_data(self, task):
                self.task = task
            def format(self):
                return self.rel
        module_map["util.format.file_name"].FileNameFormatter = Formatter
        for media, ext in (("audio-mp3", ".mp3"), ("audio-m4a", ".m4a")):
            p = driver.output_path_for(task, out, ext, media)
            check(p == out / ("custom/video" + ext), "auto preserves formatter name and audio extension")
            check(driver.output_path_for(task, out, ext, media, "auto") == p, "explicit auto preserves existing name")
            for selected in ("320", "256", "192", "128"):
                named = driver.output_path_for(task, out, ext, media, selected)
                check(named == out / "custom" / ("video [" + selected + "kbps]" + ext), "explicit bitrate suffix preserves formatter directories")
                named.write_bytes(b"existing different output")
                duplicate = driver.output_path_for(task, out, ext, media, selected)
                check(duplicate != named and duplicate.name == "video [" + selected + "kbps] (2)" + ext
                      and named.read_bytes() == b"existing different output", "bitrate output collision never overwrites existing file")
        for selected in ("auto", "320", "256", "192", "128"):
            check(driver.output_path_for(task, out, ".mp4", "video", selected) == out / "custom/video.mp4", "video naming normalizes bitrate to auto")
        Formatter.rel = None
        for selected in ("auto", "320", "256", "192", "128"):
            named = driver.output_path_for(task, out, ".mp3", "audio-mp3", selected)
            expected = "parent - part [BV1GJ411x7h7]" + ("" if selected == "auto" else " [" + selected + "kbps]") + ".mp3"
            check(named == out / expected, "fallback naming preserves titles and appends only explicit bitrate")
        for rel in ("../outside.mp4", str(fixture / "outside.mp4"), "bad\tname.mp4"):
            Formatter.rel = rel
            p = driver.output_path_for(task, out, ".mp3", "audio-mp3")
            check(p.resolve().is_relative_to(out.resolve()), "formatter outside/control path falls back safely")
        Formatter.rel = None
        job_path = media_root / "clock.json"
        job_path.write_text(json.dumps({"id": uuid.uuid4().hex}), encoding="utf-8")
        job = driver.Job(job_path)
        job.save(percent=2)
        ticks = 621355968000000000 + int(time.time() * 10000000)
        check(abs(job.data["updated"] - ticks) < 20000000, "driver updated is .NET ticks")
        for text in ("private cookie synthetic-private-value", "http://user:synthetic-private-value@proxy.invalid", "权限 synthetic-private-value", "login synthetic-private-value"):
            check("synthetic-private-value" not in driver.friendly(RuntimeError(text)), "driver friendly errors cannot expose credentials")
        check(driver.completed_files(out, [str(dash_video), str(source_audio), str(out), str(out / "missing"), str(dash_video)]) == [str(dash_video)], "driver validates output files")
        all_files = []
        for index in range(23):
            p = out / ("cap-%d.mp4" % index)
            p.write_bytes(b"complete")
            all_files.append(str(p))
        check(len(driver.completed_files(out, all_files)) == 20, "driver caps files at20")

        # Real run() lifecycle, with only source parsing/downloading replaced by local fixtures.
        for media in ("audio-mp3", "audio-m4a", "video"):
            for selected in (None, "320", "256", "192", "128"):
                job_id = uuid.uuid4().hex
                jobs_dir = media_root / ("jobs-" + job_id)
                jobs_dir.mkdir()
                output_dir = media_root / ("run-" + job_id)
                output_dir.mkdir()
                record = jobs_dir / (job_id + ".json")
                record.write_text(json.dumps({"id": job_id, "downloadDir": str(output_dir), "url": "https://www.bilibili.com/video/BV1GJ411x7h7"}), encoding="utf-8")
                args = SimpleNamespace(bili_root=str(bili_root), output_dir=str(output_dir), jobs_dir=str(jobs_dir),
                                       job_id=job_id, media=media, url="offline", part="all", quality="best")
                if selected is not None:
                    args.audio_quality = selected
                normalized = "auto" if media == "video" or selected is None else selected
                leaves = [{"title": "part A", "part_number": 1}, {"title": "part B", "part_number": 2}]
                def download(entry, work_dir, job, progress):
                    check(job.data.get("audioQuality") == normalized, "driver persists normalized bitrate before download")
                    shutil.copy2(source_audio, work_dir / entry["file_name"])
                    progress(10)
                with patch.object(driver, "setup_bili"), patch.object(driver, "parse_url", return_value=(leaves, "Veylo 合成集")), \
                     patch.object(driver, "resolve_entries", return_value=([{"file_key": "audio", "file_name": "local.m4a", "file_size": 10}], MediaType.M4A)), \
                     patch.object(driver, "download_entry", side_effect=download):
                    driver.run(args, driver.Job(record))
                result = json.loads(record.read_text(encoding="utf-8"))
                check(result["state"] == "completed" and result["percent"] == 100 and result["title"] == "Veylo 合成集", "driver lifecycle final state/title")
                check(result.get("audioQuality") == normalized, "driver completed job retains normalized bitrate")
                ext = ".mp3" if media == "audio-mp3" else ".m4a"
                check(len(result["files"]) == 2 and all(Path(p).suffix == ext for p in result["files"]), "driver lifecycle actual completed file paths")
                check(all((" [" + normalized + "kbps]") in Path(p).name for p in result["files"]) if normalized != "auto"
                      else all("kbps]" not in Path(p).name for p in result["files"]), "driver lifecycle requested bitrate filename")
                check(not (jobs_dir / (job_id + ".work")).exists(), "driver removes only its own empty work directory")
        # main() must publish failures without claiming partial output or losing earlier parts.
        for mode in ("missing-encoder", "encode-failure"):
            job_id = uuid.uuid4().hex
            jobs_dir = media_root / ("failed-jobs-" + job_id)
            jobs_dir.mkdir()
            output_dir = media_root / ("failed-out-" + job_id)
            output_dir.mkdir()
            preserved = output_dir / "earlier [128kbps].m4a"
            preserved.write_bytes(b"completed earlier part")
            record = jobs_dir / (job_id + ".json")
            record.write_text(json.dumps({"id": job_id, "downloadDir": str(output_dir), "files": [str(preserved)],
                                          "audioQuality": "128", "title": "Kept title", "state": "queued", "percent": 0}), encoding="utf-8")
            argv = ["bd_driver.py", "--bili-root", str(bili_root), "--url", "offline", "--job-id", job_id,
                    "--jobs-dir", str(jobs_dir), "--output-dir", str(output_dir), "--media", "audio-m4a", "--audio-quality", "128"]
            def fail_encode(cmd, cwd):
                Path(cmd[-1]).write_bytes(b"incomplete media")
                raise RuntimeError("FFmpeg synthetic-private-value")
            with patch.object(sys, "argv", argv), patch.object(driver, "setup_bili"), \
                 patch.object(driver, "parse_url", return_value=([{"title": "failed part"}], "Kept title")), \
                 patch.object(driver, "resolve_entries", return_value=([audio_entry], MediaType.DASH)), \
                 patch.object(driver, "download_entry"), \
                 patch.object(driver, "audio_ffmpeg", return_value=(str(synth_ffmpeg), mode != "missing-encoder")), \
                 patch.object(driver, "run_ffmpeg", side_effect=fail_encode):
                check(driver.main() == 1, "driver failure returns nonzero")
            result = json.loads(record.read_text(encoding="utf-8"))
            check(result["state"] == "failed" and result["audioQuality"] == "128" and result["title"] == "Kept title", "driver failure retains title and bitrate")
            check(result["files"] == [str(preserved)] and preserved.read_bytes() == b"completed earlier part", "driver failure preserves completed parts only")
            check(list(output_dir.iterdir()) == [preserved], "failed bitrate leaves no partial output")
            check("synthetic-private-value" not in result["error"] and ("完整 FFmpeg" in result["error"] if mode == "missing-encoder" else "FFmpeg" in result["error"]), "driver failure publishes safe actionable message")
        check(not list(out.rglob(".veylo-partial-*")), "merge leaves no private partial outputs")
        driver_feature_tests(driver, module_map, fixture, bili_root, media_root, source_audio)
    print("PASS real FFmpeg synthetic DASH/video, MP3, AAC M4A, muxed fallback and multipart extraction")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-output", type=Path)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="veylo_native_media_") as temp:
        fixture = Path(temp)
        before = CHECKS
        release = native_tests(fixture, args.release_output)
        print("NATIVE_SUITE assertions=%d" % (CHECKS - before))
        before = CHECKS
        driver_tests(fixture)
        print("DRIVER_FFMPEG_SUITE assertions=%d" % (CHECKS - before))
        if args.release_output:
            check(not args.release_output.exists(), "release staging output must be new")
            args.release_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(release, args.release_output)
            print("STAGED", str(args.release_output))
        time.sleep(.2)
    print("PASS Bili Veylo offline:", CHECKS, "assertions; no network, real profiles, Cookie values or Explorer")


if __name__ == "__main__":
    main()
