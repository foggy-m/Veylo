"""Offline Veylo driver contract tests. No Qt application, account or network."""
import ast
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(os.environ.get("VEYLO_TEST_BILI_DIR", str(Path.home() / "Desktop" / "Bili23-Downloader")))
SCRIPT = TOOLS / "script"
sys.dont_write_bytecode = True


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load(ROOT / "driver/bd_driver.py", "veylo_features_driver")
XML = load(SCRIPT / "util/parse/additional/file/danmaku_xml.py", "veylo_bili_xml")
TIME = load(SCRIPT / "util/format/time.py", "veylo_bili_time")


def packages():
    result = {}
    for name in ("util", "util.common", "util.common.enum", "util.format", "util.format.time",
                 "util.parse", "util.parse.worker", "util.parse.parser", "util.parse.parser.video",
                 "util.parse.parser.bangumi", "util.parse.parser.cheese", "util.parse.episode",
                 "util.parse.episode.bangumi", "util.parse.episode.cheese", "util.parse.additional",
                 "util.parse.additional.danmaku", "util.parse.additional.player", "util.parse.additional.subtitles",
                 "util.parse.additional.file", "util.parse.additional.file.danmaku_xml"):
        result[name] = ModuleType(name)
        result[name].__path__ = []
    result["util.common.enum"].MediaType = SimpleNamespace(DASH=1, MP4=2, FLV=3, M4A=4)
    result["util.format.time"].Time = TIME.Time
    result["util.parse.additional.file.danmaku_xml"].DanmakuXML = XML.DanmakuXML
    return result


class DriverFeatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="veylo_features_offline_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.mods = packages()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, self.mods))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("network is forbidden")))
        self.stack.enter_context(patch.object(socket, "create_connection", side_effect=AssertionError("network is forbidden")))

    def episode(self, **kw):
        return SimpleNamespace(Episode=SimpleNamespace(cid=7, duration=361, bvid="BV1GJ411x7h7", aid=8, **kw))

    def record(self, **kw):
        jid = uuid.uuid4().hex
        path = self.jobs / (jid + ".json")
        path.write_text(json.dumps({"id": jid, "downloadDir": str(self.output), "state": "queued", "percent": 0, "files": [], **kw}), encoding="utf-8")
        args = SimpleNamespace(bili_root=str(self.root), output_dir=str(self.output), jobs_dir=str(self.jobs),
                               job_id=jid, url="offline", part="collection", quality="best", media="video",
                               audio_quality="auto", danmaku="off", subtitles="off")
        return args, DRIVER.Job(path)

    def leaf(self, index, **kw):
        return {"title": "entry-%03d" % index, "cid": index + 1, "_collection": True, **kw}

    def run_fixture(self, leaves, args=None, job=None, fail=(), **patches):
        args, job = (args, job) if args is not None else self.record()
        def task(leaf, parent, media):
            return SimpleNamespace(Episode=SimpleNamespace(cid=leaf["cid"], leaf_title=leaf["title"]))
        def resolve(task, quality, media):
            if task.Episode.cid in fail:
                raise RuntimeError("offline denied synthetic-private-value")
            return [{"file_key": "audio", "file_name": "source-%d.m4a" % task.Episode.cid, "file_size": 1}], 4
        def download(entry, work, job, progress):
            (work / entry["file_name"]).write_bytes(b"synthetic source")
            progress(1)
        def output(task, directory, ext, media, audio_quality):
            directory.mkdir(parents=True, exist_ok=True)
            return directory / (task.Episode.leaf_title + ext)
        def merge(task, entries, media_type, work, output, root, media, audio_quality):
            output.write_bytes(b"completed media")
        values = {"setup_bili": lambda root: None, "parse_url": lambda url, part: (leaves, "Collection: title"),
                  "make_task": task, "resolve_entries": resolve, "download_entry": download,
                  "output_path_for": output, "merge": merge, **patches}
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(patch.object(DRIVER, name, side_effect=value))
            DRIVER.run(args, job)
        return args, job

    def test_xml_uses_bili23_writer_and_millisecond_timestamps(self):
        entries = [{"stime": 1500, "mode": 1, "size": 25, "color": 0, "text": "a<&\"'\x01", "dmid": "17"}]
        self.assertEqual(DRIVER.danmaku_xml(entries, 7), XML.DanmakuXML(entries, 7).generate())
        tree = ET.fromstring(DRIVER.danmaku_xml(entries, 7))
        self.assertEqual(tree.find("d").attrib["p"].split(",")[:4], ["1.50000", "1", "25", "0"])

    def test_xml_reuses_bili23_class_not_a_parallel_serializer(self):
        writer = self.mods["util.parse.additional.file.danmaku_xml"].DanmakuXML
        with patch.object(writer, "generate", return_value="bili23-xml") as generate:
            self.assertEqual(DRIVER.danmaku_xml([], 7), "bili23-xml")
        generate.assert_called_once()

    def test_ass_is_deterministic_without_mutating_input(self):
        entries = [{"stime": 2000, "text": "later"}, {"stime": 1000, "text": "first", "mode": 5}]
        saved = json.dumps(entries)
        self.assertEqual(DRIVER.danmaku_ass(entries, "title"), DRIVER.danmaku_ass(list(reversed(entries)), "title"))
        self.assertEqual(json.dumps(entries), saved)

    def test_ass_resolution_font_color_and_size(self):
        text = DRIVER.danmaku_ass([{"stime": 1000, "text": "text", "size": 42, "color": 0x112233}])
        for expected in ("PlayResX: 1920", "PlayResY: 1080", "Microsoft YaHei", r"\fs42", r"\c&H332211&"):
            self.assertIn(expected, text)

    def test_ass_scrolling_is_right_to_left_for_eight_seconds(self):
        row = DRIVER.danmaku_ass([{"stime": 1000, "text": "scroll"}]).split("Dialogue: ")[1]
        self.assertTrue(row.startswith("0,0:00:01.00,0:00:09.00,"))
        move = re.search(r"\\move\((\d+),(\d+),(-?\d+),(\d+)\)", row)
        self.assertIsNotNone(move)
        self.assertGreater(int(move[1]), int(move[3]))
        self.assertEqual(move[2], move[4])

    def test_ass_modes_four_and_five_are_fixed_for_five_seconds(self):
        text = DRIVER.danmaku_ass([{"stime": 2000, "mode": 4, "text": "bottom"}, {"stime": 2000, "mode": 5, "text": "top"}])
        self.assertEqual(text.count("0:00:02.00,0:00:07.00"), 2)
        self.assertIn(r"\an2\pos(960,", text)
        self.assertIn(r"\an8\pos(960,", text)
        self.assertNotIn(r"\move", text)

    def test_ass_escapes_braces_slashes_and_preserves_line_breaks(self):
        text = DRIVER.danmaku_ass([{"stime": 1, "text": "a{b}\\c\r\nd"}], "safe\n[Events]")
        self.assertIn(r"a\{b\}\\c\Nd", text)
        self.assertEqual(text.count("\n[Events]\n"), 1)

    def test_ass_lanes_stay_in_the_1080p_frame_at_large_font_sizes(self):
        text = DRIVER.danmaku_ass([{"stime": 1000, "size": 72, "text": "row %02d" % i} for i in range(30)])
        ys = [int(y) for y in re.findall(r"\\move\(1920,(\d+),", text)]
        self.assertTrue(ys)
        self.assertTrue(all(0 <= y <= 1008 for y in ys), ys)
        self.assertGreater(len(set(ys)), 1)

    def test_ass_converts_millisecond_time(self):
        self.assertEqual(DRIVER.format_ass_time(3661234), "1:01:01.23")

    def test_srt_matches_existing_bili23_algorithm(self):
        data = {"body": [{"from": 0, "to": 1.234, "content": "line one"},
                         {"from": 59.9996, "to": 3600.5, "content": "line\ntwo"},
                         {"from": 3601, "to": 3602, "content": ""}]}
        lines = []
        for index, item in enumerate(data["body"], 1):
            lines.extend((str(index), "%s --> %s" % (TIME.Time.format_srt_time(item["from"]), TIME.Time.format_srt_time(item["to"])), item["content"] + "\n"))
        self.assertEqual(DRIVER.subtitle_srt(data), "\n".join(lines).strip())

    def test_srt_fallback_formats_carry_and_hours(self):
        self.assertEqual(DRIVER.format_srt_time(59.9996), "00:01:00,000")
        self.assertEqual(DRIVER.format_srt_time(3661.5), "01:01:01,500")
        self.assertEqual(DRIVER.subtitle_srt({}), "")

    def test_language_is_bounded_ascii_and_not_a_path(self):
        for raw in ("zh-CN", "ai-zh", "../../bad", "x" * 80, "\x00中文", ""):
            self.assertRegex(DRIVER.safe_language(raw), r"^[A-Za-z0-9_-]{1,20}$")
        self.assertEqual(DRIVER.safe_language("zh-CN"), "zh-CN")

    def test_off_extras_do_not_fetch_or_import_parsers(self):
        with patch.object(DRIVER, "fetch_danmaku") as dm, patch.object(DRIVER, "fetch_subtitles") as sub:
            paths, errors = DRIVER.download_extras(self.episode(), self.output / "file.mp4")
        self.assertEqual((paths, errors), ([], set()))
        self.assertFalse(dm.called or sub.called)

    def test_extras_files_share_the_media_stem_including_audio(self):
        for suffix in (".mp4", ".m4a", ".opus"):
            with self.subTest(suffix=suffix):
                path = self.output / ("file" + suffix)
                paths, errors = DRIVER.download_extras(self.episode(), path, "xml", "srt",
                    lambda task: [{"stime": 1000, "text": "dm"}],
                    lambda task: [{"language": "zh-CN", "data": {"body": [{"from": 0, "to": 1, "content": "sub"}]}}])
                self.assertEqual(errors, set())
                self.assertEqual([p.name for p in paths], ["file.danmaku.xml", "file.zh-CN.srt"])
                self.assertTrue(all(p.is_file() for p in paths))
                for p in paths:
                    p.unlink()

    def test_extras_independent_failures_are_not_media_errors(self):
        def fail(task):
            raise RuntimeError("synthetic-private-value")
        paths, errors = DRIVER.download_extras(self.episode(), self.output / "file.mp4", "ass", "srt", fail, fail)
        self.assertEqual(paths, [])
        self.assertEqual(errors, {"danmaku", "subtitles"})

    def test_fetcher_typeerror_is_not_retried_with_a_different_object(self):
        calls = []
        def fail(task):
            calls.append(task)
            raise TypeError("internal fetcher error")
        DRIVER.download_extras(self.episode(), self.output / "file.mp4", "ass", "off", fail)
        self.assertEqual(len(calls), 1)

    def test_no_subtitles_is_success_and_creates_no_empty_sidecar(self):
        paths, errors = DRIVER.download_extras(self.episode(), self.output / "file.mp4", "off", "srt", subtitle_fetcher=lambda task: [])
        self.assertEqual((paths, errors), ([], set()))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_subtitle_failure_keeps_other_successful_languages(self):
        output = self.output / "file.mp4"
        with patch.object(DRIVER, "write_subtitle", side_effect=[RuntimeError("one language unavailable"), output.with_name("file.en.srt")]) as write:
            _, errors = DRIVER.download_extras(self.episode(), output, "off", "srt", subtitle_fetcher=lambda task: [
                {"language": "zh-CN", "data": {"body": [{"from": 0, "to": 1, "content": "zh"}]}},
                {"language": "en", "data": {"body": [{"from": 0, "to": 1, "content": "en"}]}}])
        self.assertEqual(write.call_count, 2)
        self.assertEqual(errors, {"subtitles"})

    def test_canceled_fetch_is_not_swallowed_as_an_optional_warning(self):
        def canceled(task):
            raise DRIVER.JobCanceled("已取消下载。")
        with self.assertRaises(DRIVER.JobCanceled):
            DRIVER.download_extras(self.episode(), self.output / "file.mp4", "ass", "off", canceled)

    def test_failed_sidecar_write_removes_its_private_partial(self):
        target = self.output / "file.danmaku.ass"
        with patch.object(DRIVER.os, "replace", side_effect=OSError("offline disk error")):
            with self.assertRaises(OSError):
                DRIVER._write_extra(target, "partial")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_default_danmaku_adapter_uses_existing_bili_parser(self):
        task = self.episode()
        calls = []
        class Parser:
            def __init__(self, value):
                calls.append(value)
            def _get_all_protobuf_parts(self):
                return [{"stime": 1000, "text": "real seam"}]
        self.mods["util.parse.additional.danmaku"].DanmakuParser = Parser
        self.assertEqual(DRIVER.fetch_danmaku(task), [{"stime": 1000, "text": "real seam"}])
        self.assertEqual(calls, [task])

    def test_default_subtitle_adapter_uses_existing_player_and_network_parser(self):
        urls = []
        class Player:
            def __init__(self, task):
                pass
            def get_data(self):
                return {"subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": "//fixture.invalid/sub.json"}]}}
        class Subtitles:
            def __init__(self, task):
                pass
            def _get_subtitles_data(self, url):
                urls.append(url)
                return {"body": []}
        self.mods["util.parse.additional.player"].PlayerInfoParser = Player
        self.mods["util.parse.additional.subtitles"].SubtitlesParser = Subtitles
        self.assertEqual(DRIVER.fetch_subtitles(self.episode()), [{"language": "zh-CN", "data": {"body": []}}])
        self.assertEqual(urls, ["https://fixture.invalid/sub.json"])

    def test_collection_expansion_is_one_leaf_per_episode_with_own_ids(self):
        info = {"data": {"aid": 1, "cid": 1, "bvid": "base", "title": "base", "duration": 9,
                         "ugc_season": {"title": "Season", "sections": [{"episodes": [
                             {"aid": 2, "cid": 20, "bvid": "episode", "title": "episode", "duration": 25,
                              "pages": [{"cid": 20}, {"cid": 21}]}]}]}}}
        leaves = DRIVER.video_leaves(info, "collection", 1)
        self.assertEqual(len(leaves), 1)
        self.assertEqual((leaves[0]["aid"], leaves[0]["cid"], leaves[0]["bvid"], leaves[0]["duration"]), (2, 20, "episode", 25))
        self.assertEqual(leaves[0]["_collection_title"], "Season")

    def test_collection_view_data_has_a_hard_200_leaf_cap(self):
        info = {"data": {"ugc_season": {"title": "Season", "sections": [{"episodes": [
            {"aid": i, "cid": i, "bvid": "b%d" % i, "title": "e%d" % i} for i in range(205)]}]}}}
        leaves = DRIVER.video_leaves(info, "collection", 1)
        self.assertEqual(len(leaves), 200)
        self.assertEqual(leaves[-1]["cid"], 199)

    def test_collection_fallback_is_all_parts_not_current_page(self):
        info = {"data": {"cid": 9, "bvid": "base", "title": "video", "pages": [
            {"cid": 11, "page": 1, "part": "A"}, {"cid": 12, "page": 2, "part": "B"}]}}
        leaves = DRIVER.video_leaves(info, "collection", 2)
        self.assertEqual([p["cid"] for p in leaves], [11, 12])
        self.assertTrue(leaves[0]["_collection_fallback"])

    def test_bangumi_and_cheese_collection_use_all_leaves_and_cap(self):
        for kind in ("bangumi", "cheese"):
            with self.subTest(kind=kind):
                self.mods["util.parse.worker"].WorkerBase = lambda: SimpleNamespace(get_parser_type=lambda url: kind)
                class Parser:
                    def find_str(self, pattern, url):
                        return "ss"
                    def get_season_id(self):
                        return 7
                    def get_bangumi_info(self, value):
                        self.info_data = {"result": {"season_title": "Season"}}
                    def get_category_name(self):
                        return kind
                class Episodes:
                    def __init__(self, *args):
                        pass
                    def parse(self, update_episode_list):
                        assert update_episode_list is False
                        return SimpleNamespace(get_all_leaves=lambda: [SimpleNamespace(to_dict=lambda i=i: {"ep_id": i, "title": "e%d" % i}) for i in range(205)])
                setattr(self.mods["util.parse.parser." + kind], "BangumiParser" if kind == "bangumi" else "CheeseParser", Parser)
                setattr(self.mods["util.parse.episode." + kind], "BangumiEpisodeParser" if kind == "bangumi" else "CheeseEpisodeParser", Episodes)
                leaves, title = DRIVER.parse_url("https://www.bilibili.com/%s/play/ss7" % kind, "collection")
                self.assertEqual((len(leaves), title), (200, "Season"))
                self.assertTrue(all(leaf["_collection"] for leaf in leaves))

    def test_run_collection_cap_subfolder_progress_and_file_count(self):
        args, job = self.run_fixture([self.leaf(i) for i in range(205)])
        self.assertEqual(job.data["state"], "completed")
        self.assertEqual(job.data["totalEpisodes"], 200)
        self.assertEqual(job.data["fileCount"], 200)
        self.assertEqual(job.data["percent"], 100)
        self.assertEqual(len(job.data["files"]), 20)
        self.assertEqual(len(list(self.output.rglob("*.m4a"))), 200)
        self.assertTrue(all(Path(p).parent == self.output / "Collection_ title" for p in job.data["files"]))
        self.assertFalse((self.jobs / (args.job_id + ".work")).exists())

    def test_run_fallback_records_the_whitelisted_warning(self):
        args, job = self.run_fixture([self.leaf(0, _collection=False, _collection_fallback=True)])
        self.assertEqual(job.data["warnings"], [DRIVER.WARN_FALLBACK])
        self.assertEqual(Path(job.data["files"][0]).parent, self.output)

    def test_run_partial_failure_continues_to_later_entries(self):
        _, job = self.run_fixture([self.leaf(i) for i in range(3)], fail=(2,))
        self.assertEqual(job.data["state"], "completed")
        self.assertEqual(job.data["fileCount"], 2)
        self.assertEqual([Path(p).stem for p in job.data["files"]], ["entry-000", "entry-002"])
        self.assertEqual(job.data["warnings"], [DRIVER.WARN_PARTIAL])

    def test_all_failed_leaves_fail_the_job(self):
        with self.assertRaises(RuntimeError):
            self.run_fixture([self.leaf(i) for i in range(3)], fail=(1, 2, 3))
        self.assertEqual(list(self.output.rglob("*.m4a")), [])

    def test_run_extras_failure_keeps_successful_media_and_warnings(self):
        args, job = self.record()
        args.danmaku, args.subtitles = "ass", "srt"
        def fail(task):
            raise RuntimeError("synthetic-private-value")
        args.danmaku_fetcher = args.subtitle_fetcher = fail
        self.run_fixture([self.leaf(0)], args, job)
        self.assertEqual(job.data["state"], "completed")
        self.assertEqual(job.data["warnings"], [DRIVER.WARN_DANMAKU, DRIVER.WARN_SUBTITLE])
        self.assertEqual(job.data["fileCount"], 1)
        self.assertNotIn("synthetic-private-value", json.dumps(job.data))

    def test_media_files_take_priority_over_extras_at_the_20_file_cap(self):
        args, job = self.record()
        args.danmaku, args.subtitles = "ass", "srt"
        args.danmaku_fetcher = lambda task: [{"stime": 1000, "text": "dm"}]
        args.subtitle_fetcher = lambda task: [{"language": "zh-CN", "data": {"body": [{"from": 0, "to": 1, "content": "text"}]}}]
        self.run_fixture([self.leaf(i) for i in range(12)], args, job)
        self.assertEqual(job.data["fileCount"], 36)
        self.assertEqual(len(job.data["files"]), 20)
        self.assertTrue(all(Path(p).suffix == ".m4a" for p in job.data["files"][:12]))
        self.assertTrue(all(Path(p).suffix in (".ass", ".srt") for p in job.data["files"][12:]))

    def test_failed_video_merge_does_not_leave_a_final_partial(self):
        def merge(task, entries, media_type, work, output, root, media, quality):
            output.write_bytes(b"incomplete video")
            raise RuntimeError("FFmpeg fixture failure")
        with self.assertRaises(RuntimeError):
            self.run_fixture([self.leaf(0)], merge=merge)
        self.assertEqual([p for p in self.output.rglob("*") if p.is_file()], [])

    def test_marker_during_final_extras_prevents_completed_state(self):
        args, job = self.record()
        def extras(*a, **kw):
            (self.jobs / (args.job_id + ".cancel")).write_bytes(b"")
            return [], set()
        with self.assertRaises(DRIVER.JobCanceled):
            self.run_fixture([self.leaf(0)], args, job, download_extras=extras)
        self.assertNotEqual(job.data["state"], "completed")
        self.assertEqual(len(job.data["files"]), 1)

    def test_main_canceled_error_matches_native_whitelist(self):
        args, job = self.record()
        argv = ["bd_driver.py", "--bili-root", str(self.root), "--url", "offline", "--job-id", args.job_id,
                "--jobs-dir", str(self.jobs), "--output-dir", str(self.output)]
        with patch.object(sys, "argv", argv), patch.object(DRIVER, "run", side_effect=DRIVER.JobCanceled("已取消下载。")):
            self.assertEqual(DRIVER.main(), 1)
        saved = json.loads(job.path.read_text(encoding="utf-8"))
        self.assertEqual((saved["state"], saved["error"]), ("canceled", "已取消下载。"))

    def test_main_cancel_preserves_total_beyond_public_file_cap(self):
        outputs = [self.output / ("completed-%02d.mp4" % index) for index in range(27)]
        for path in outputs:
            path.write_bytes(b"complete")
        public = [str(path) for path in outputs[:20]]
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"outside")
        for stored, expected in ((27, 27), ("missing", 20), (None, 20), ("27", 20), (-1, 20), (100001, 20), (True, 20)):
            with self.subTest(file_count=stored):
                args, job = self.record(state="downloading", percent=67.5, files=public)
                if stored != "missing":
                    job.save(fileCount=stored)
                else:
                    job.save(files=[str(outside), str(self.output / "missing.mp4"), *public])
                marker = self.jobs / (args.job_id + ".cancel")
                marker.write_bytes(b"")
                argv = ["bd_driver.py", "--bili-root", str(self.root), "--url", "offline", "--job-id", args.job_id,
                        "--jobs-dir", str(self.jobs), "--output-dir", str(self.output)]
                with patch.object(sys, "argv", argv), patch.object(DRIVER, "setup_bili") as setup:
                    self.assertEqual(DRIVER.main(), 1)
                setup.assert_not_called()
                saved = json.loads(job.path.read_text(encoding="utf-8"))
                self.assertEqual((saved["state"], saved["error"], saved["percent"]), ("canceled", "已取消下载。", 67.5))
                self.assertEqual(saved["fileCount"], expected)
                self.assertEqual(saved["files"], public)
                self.assertTrue(all(Path(path).is_file() for path in saved["files"]))
                self.assertTrue(all(path.read_bytes() == b"complete" for path in outputs))
                self.assertFalse(marker.exists())

    def test_queued_marker_prevents_setup_or_work_creation(self):
        args, job = self.record()
        (self.jobs / (args.job_id + ".cancel")).write_bytes(b"")
        with patch.object(DRIVER, "setup_bili") as setup:
            with self.assertRaises(DRIVER.JobCanceled):
                DRIVER.run(args, job)
        self.assertFalse(setup.called)
        self.assertFalse((self.jobs / (args.job_id + ".work")).exists())

    def test_new_driver_fields_validate_before_setup_or_work(self):
        for field in ("danmaku", "subtitles", "part"):
            for bad in (None, True, 1, "invalid", [], {}):
                args, job = self.record()
                setattr(args, field, bad)
                with patch.object(DRIVER, "setup_bili") as setup:
                    with self.assertRaises(ValueError):
                        DRIVER.run(args, job)
                self.assertFalse(setup.called)
                self.assertFalse((self.jobs / (args.job_id + ".work")).exists())

    def test_no_qt_application_or_configuration_mutation_calls(self):
        tree = ast.parse((ROOT / "driver/bd_driver.py").read_text(encoding="utf-8"))
        forbidden = {"QApplication", "QCoreApplication", "setApplicationName", "setApplicationDisplayName", "setOrganizationName", "setOrganizationDomain"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                self.assertNotIn(name, forbidden)
                self.assertFalse(isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                                 and node.func.value.id == "config" and node.func.attr in ("set", "save", "load"))

    def test_real_bili_protobuf_xml_player_and_srt_seams_offline(self):
        # Real installed modules, but stub their package-level config/Qt/network imports before executing them.
        code = r'''
import importlib.util, json, socket, sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
sys.dont_write_bytecode = True
root, tools = map(Path, sys.argv[1:])
sys.path.insert(0, str(tools / "site-packages"))
script = tools / "script"
def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, script / rel)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value
for name in ("util", "util.misc", "util.common", "util.common._json", "util.common.enum", "util.common.config", "util.common.translator", "util.network", "util.network.request", "util.download", "util.download.task", "util.download.task.info", "util.format", "util.format.time", "util.parse", "util.parse.additional", "util.parse.additional.base", "util.parse.additional.file", "util.parse.additional.file.danmaku_ass", "util.parse.additional.file.subtitle_ass"):
    sys.modules[name] = ModuleType(name)
    sys.modules[name].__path__ = []
class Base:
    def __init__(self, task): self.task_info = task
    def enc_wbi(self, params):
        requests.append(dict(params))
        return "synthetic-signed-query"
requests, urls = [], []
sys.modules["util.parse.additional.base"].AdditionalParserBase = Base
sys.modules["util.download.task.info"].TaskInfo = object
sys.modules["util.common.translator"].Translator = SimpleNamespace()
sys.modules["util.common._json"].json_dumps = json.dumps
sys.modules["util.common.enum"].DanmakuType = SimpleNamespace()
sys.modules["util.common.enum"].SubtitleType = SimpleNamespace()
sys.modules["util.common.config"].config = SimpleNamespace()
sys.modules["util.parse.additional.file.danmaku_ass"].DanmakuASS = object
sys.modules["util.parse.additional.file.subtitle_ass"].SubtitlesASS = object
pb = load("util.misc.dm_pb2", "util/misc/dm_pb2.py")
load("util.parse.additional.file.danmaku_xml", "util/parse/additional/file/danmaku_xml.py")
load("util.format.time", "util/format/time.py")
class ResponseType: BYTES = 2
class Request:
    def __init__(self, url, response_type=None): self.url = url
    def run(self):
        urls.append(self.url)
        if "seg.so?" in self.url:
            reply = pb.DmSegMobileReply()
            reply.elems.add(stime=1500, text="offline protobuf", mode=1, size=25, color=0x112233)
            return reply.SerializeToString()
        if "wbi/v2?" in self.url:
            return {"data": {"subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": "//fixture.invalid/sub.json"}]}}}
        return {"body": [{"from": 0, "to": 1, "content": "offline subtitle"}]}
sys.modules["util.network.request"].ResponseType = ResponseType
sys.modules["util.network.request"].SyncNetWorkRequest = Request
load("util.parse.additional.danmaku", "util/parse/additional/danmaku.py")
load("util.parse.additional.player", "util/parse/additional/player.py")
load("util.parse.additional.subtitles", "util/parse/additional/subtitles.py")
spec = importlib.util.spec_from_file_location("driver", root / "driver/bd_driver.py")
driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
task = SimpleNamespace(Episode=SimpleNamespace(cid=7, duration=361, bvid="BV1GJ411x7h7", aid=8))
with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
    entries = driver.fetch_danmaku(task)
    assert len(entries) == 2 and entries[0]["stime"] == 1500
    assert requests[:2] == [{"type": 1, "oid": 7, "segment_index": 1}, {"type": 1, "oid": 7, "segment_index": 2}]
    assert "1.50000" in driver.danmaku_xml(entries, 7)
    subs = driver.fetch_subtitles(task)
    assert subs[0]["language"] == "zh-CN"
    assert driver.subtitle_srt(subs[0]["data"]) == "1\n00:00:00,000 --> 00:00:01,000\noffline subtitle"
    assert requests[2]["cid"] == 7 and requests[2]["bvid"] == "BV1GJ411x7h7" and requests[2]["dm_img_list"] == "[]"
    assert urls[0].startswith("https://api.bilibili.com/x/v2/dm/wbi/web/seg.so?")
    assert urls[2].startswith("https://api.bilibili.com/x/player/wbi/v2?")
print("PASS real installed protobuf/XML/player/SRT; synthetic bytes and injected signed network only")
'''
        result = subprocess.run([str(TOOLS / "runtime/python.exe"), "-I", "-c", code, str(ROOT), str(TOOLS)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-2000:])
        self.assertIn(b"PASS real installed protobuf/XML/player/SRT", result.stdout)


def run_suite(verbosity=2):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DriverFeatures)
    return unittest.TextTestRunner(verbosity=verbosity).run(suite)


if __name__ == "__main__":
    result = run_suite()
    print("DRIVER_FEATURE_SUITE tests=%d failures=%d errors=%d skipped=%d" %
          (result.testsRun, len(result.failures), len(result.errors), len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() else 1)
