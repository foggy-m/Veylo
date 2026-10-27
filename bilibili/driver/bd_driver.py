# -*- coding: utf-8 -*-
r"""Bili23 无界面下载驱动（Veylo 本机桥接调用）。

用 Bili23-Downloader 便携版自带的 runtime\python.exe 运行本脚本，
复用它 script\util 下的解析（含 wbi 签名、登录 Cookie）与取流逻辑，
下载与 ffmpeg 合并在本脚本内完成，进度写回桥接的任务 JSON。

不修改 Bili23 的任何文件与配置，只读取它的 config.json 与登录状态。
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from queue import Empty, SimpleQueue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from threading import Event, Lock
import time
import traceback
import uuid
from pathlib import Path

FLUENT_PATHS = Path(__file__).resolve().parents[2] / "native" / "fluent_paths.json"

CHUNK = 1 << 16  # 64 KiB keeps progress responsive on slower connections.
RETRIES = 3
RANGE_SIZE = 4 << 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"

QUALITY_TARGET = {
    "best": 999,
    "120": 120,   # 4K
    "112": 112,   # 1080P+
    "80": 80,     # 1080P
    "64": 64,     # 720P
    "32": 32,     # 480P
    "16": 16,     # 360P
}


AUDIO_ENCODER_ERROR = "指定音频码率需要支持 aac/libmp3lame 的完整 FFmpeg；请准备完整编码器或改选自动音质后重试。"
ORIGINAL_TOOLS_ERROR = "原音轨需要现有完整 FFmpeg 和同目录 ffprobe；请准备工具后重试，或选择 MP3/M4A 转换。"
ORIGINAL_COPY_ERROR = "无法确认或无损封装此原音轨；请检查媒体流，或选择 MP3/M4A 转换。"
WARN_DANMAKU = "弹幕下载失败，视频已保存。"
WARN_SUBTITLE = "字幕下载失败，视频已保存。"
WARN_FALLBACK = "该视频不属于合集，已按全部分 P 下载。"
WARN_PARTIAL = "部分条目下载失败。"
WARNINGS = (WARN_DANMAKU, WARN_SUBTITLE, WARN_FALLBACK, WARN_PARTIAL)
MAX_COLLECTION_LEAVES = 200
MAX_LANGUAGE_LENGTH = 20


class OriginalCopyError(RuntimeError):
    pass


class OriginalToolsUnavailable(RuntimeError):
    pass


class AudioEncoderUnavailable(RuntimeError):
    pass


class JobCanceled(RuntimeError):
    pass


class RangeUnavailable(RuntimeError):
    pass


class Job:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self._last_write = 0.0
        self._speed_started = time.monotonic()
        self._speed_bytes = 0

    def start_transfer(self):
        self._speed_started = time.monotonic()
        self._speed_bytes = 0
        self.save(speedBytesPerSecond=None)

    def transfer_progress(self, size):
        self._speed_bytes += size
        now = time.monotonic()
        elapsed = now - self._speed_started
        if elapsed >= 1.0:
            self.data.update(speedBytesPerSecond=self._speed_bytes / elapsed,
                             speedUpdated=int(time.time() * 10_000_000) + 621355968000000000)
            self._speed_started = now
            self._speed_bytes = 0

    def stop_transfer(self):
        self.save(speedBytesPerSecond=None)

    def save(self, force=True, **kw):
        if kw.get("state", self.data.get("state")) != "downloading":
            kw["speedBytesPerSecond"] = None
        now = time.monotonic()
        if not force and now - self._last_write < 1.0:
            self.data.update(kw)
            return
        self.data.update(kw)
        self.data["updated"] = int(time.time() * 10_000_000) + 621355968000000000
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        for attempt in range(4):
            try:
                os.replace(temp, self.path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(.025 * (attempt + 1))
        self._last_write = now


def sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "")
    name = name.strip(" .")
    return name or "video"


def add_warning(job: Job, warning: str):
    if warning not in WARNINGS:
        return
    values = job.data.get("warnings")
    if not isinstance(values, list):
        values = []
    if warning not in values:
        values.append(warning)
    job.save(warnings=values)


def cancel_marker(jobs_dir: Path, job_id: str) -> Path:
    return jobs_dir / (job_id + ".cancel")


def cancel_requested(jobs_dir: Path, job_id: str) -> bool:
    return cancel_marker(jobs_dir, job_id).is_file()


def check_canceled(jobs_dir: Path, job_id: str):
    if cancel_requested(jobs_dir, job_id):
        raise JobCanceled("已取消下载。")


def safe_language(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or ""))[:MAX_LANGUAGE_LENGTH]
    return text or "und"


def format_srt_time(seconds) -> str:
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        value = 0.0
    millis = int(round(value * 1000.0))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, secs, millis)


def subtitle_srt(data: dict) -> str:
    try:
        from util.format.time import Time
        formatter = Time.format_srt_time
    except ImportError:
        formatter = format_srt_time
    lines = []
    for index, item in enumerate(data.get("body", []), 1):
        start = item.get("from", 0)
        end = item.get("to", 0)
        content = item.get("content", "")
        lines.extend((str(index), "%s --> %s" % (formatter(start), formatter(end)), content + "\n"))
    return "\n".join(lines).strip()


def _ass_text(value: str) -> str:
    return (str(value or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N"))


def _ass_color(value) -> str:
    try:
        color = int(value)
    except (TypeError, ValueError):
        color = 0xFFFFFF
    color = max(0, min(0xFFFFFF, color))
    red = (color >> 16) & 0xFF
    green = (color >> 8) & 0xFF
    blue = color & 0xFF
    return "&H%02X%02X%02X&" % (blue, green, red)


def format_ass_time(milliseconds) -> str:
    try:
        value = max(0, int(round(float(milliseconds))))
    except (TypeError, ValueError):
        value = 0
    hours, value = divmod(value, 3600000)
    minutes, value = divmod(value, 60000)
    seconds, millis = divmod(value, 1000)
    return "%d:%02d:%02d.%02d" % (hours, minutes, seconds, millis // 10)


def danmaku_ass(entries, title="") -> str:
    """Deterministic ASS writer without Qt font metrics or application setup."""
    header = """[Script Info]
ScriptType: v4.00+
Title: %s
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,7,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % _ass_text(title)
    rows = []
    lanes = {"top": [0] * 18, "bottom": [0] * 18, "scroll": [0] * 24}
    ordered = sorted((entry for entry in (entries or []) if isinstance(entry, dict)),
                     key=lambda item: (int(item.get("stime", 0) or 0), str(item.get("text", ""))))
    for entry in ordered:
        text = _ass_text(entry.get("text", ""))
        if not text:
            continue
        try:
            start = max(0, int(entry.get("stime", 0) or 0))
        except (TypeError, ValueError):
            start = 0
        mode = int(entry.get("mode", 1) or 1)
        duration = 5000 if mode in (4, 5) else 8000
        end = start + duration
        size = max(12, min(72, int(entry.get("size", 36) or 36)))
        color = _ass_color(entry.get("color", 0xFFFFFF))
        tag = "\\fs%d\\c%s" % (size, color)
        group = "top" if mode == 5 else "bottom" if mode == 4 else "scroll"
        # Fixed 80px slots fit even the maximum 72px font without Qt metrics.
        lane_count = max(1, (1000 - size) // 80 + 1)
        available = lanes[group][:lane_count]
        lane = next((i for i, until in enumerate(available) if until <= start),
                    min(range(lane_count), key=lambda i: available[i]))
        lanes[group][lane] = end
        if mode == 5:
            tag += "\\an8\\pos(960,%d)" % (40 + lane * 80)
        elif mode == 4:
            tag += "\\an2\\pos(960,%d)" % (1040 - lane * 80)
        else:
            y = 40 + lane * 80
            tag += "\\move(1920,%d,-600,%d)" % (y, y)
        rows.append("Dialogue: 0,%s,%s,Default,,0,0,0,,{%s}%s" %
                    (format_ass_time(start), format_ass_time(end), tag, text))
    return header + "\n".join(rows) + ("\n" if rows else "")


def danmaku_xml(entries, cid) -> str:
    from util.parse.additional.file.danmaku_xml import DanmakuXML
    return DanmakuXML(entries, cid).generate()


def completed_file_count(output_dir: Path, paths) -> int:
    """Validate all owned output paths without the public 20-file response cap."""
    root = output_dir.resolve(strict=True)
    result = set()
    if not root.is_dir() or str(root).startswith("\\\\"):
        return 0
    for raw in paths:
        if not isinstance(raw, (str, Path)) or any(ord(c) < 32 or ord(c) == 127 for c in str(raw)):
            continue
        try:
            candidate = Path(raw)
            path = candidate.resolve(strict=True)
            if not candidate.is_absolute() or path == root or not path.is_relative_to(root) or not path.is_file():
                continue
            if any(p.is_symlink() or (getattr(p, "is_junction", lambda: False)()) for p in [candidate, *candidate.parents]):
                continue
            result.add(str(path))
        except (OSError, ValueError):
            continue
    return min(len(result), 100000)


def cleanup_canceled(work: Path, output_dir: Path = None, job_id: str = ""):
    if work.is_dir() and not work.is_symlink():
        shutil.rmtree(work, ignore_errors=True)
    if output_dir is None or not job_id or not output_dir.is_dir():
        return
    for path in output_dir.rglob("*"):
        if path.name.startswith(".veylo-original-%s-" % job_id) or path.name.startswith(".veylo-partial-%s-" % job_id):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file() and not path.is_symlink():
                try:
                    path.unlink()
                except OSError:
                    pass


def friendly(exc: Exception) -> str:
    if isinstance(exc, JobCanceled):
        return "已取消下载。"
    if isinstance(exc, OriginalToolsUnavailable):
        return ORIGINAL_TOOLS_ERROR
    if isinstance(exc, OriginalCopyError):
        return ORIGINAL_COPY_ERROR
    if isinstance(exc, AudioEncoderUnavailable):
        return AUDIO_ENCODER_ERROR
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if "login" in lowered or "登录" in text or "-101" in text:
        return "需要登录：请在 Bili23 中重新登录 B 站后重试。"
    if "大会员" in text or "权限" in text or "-403" in text:
        return "内容不可访问或需要相应权限，请在 Bili23 中确认账号权限。"
    if "-404" in text:
        return "内容不存在或不可访问。"
    if "timeout" in lowered or "connect" in lowered:
        return "网络请求失败，请检查网络或代理设置。"
    if "ffmpeg" in lowered:
        return "FFmpeg 处理失败，请检查媒体流和保存目录。"
    return "下载失败，请检查内容可访问性、Bili23 内核和保存目录。"


def normalized_audio_quality(media: str, value="auto") -> str:
    if not isinstance(value, str) or value not in ("auto", "320", "256", "192", "128"):
        raise ValueError("不支持的下载选项：audioQuality。")
    return "auto" if media in ("video", "audio-original") else value


def build_argparser():
    ap = argparse.ArgumentParser(description="Veylo · Bili23 下载驱动")
    ap.add_argument("--bili-root", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--part", default="current", choices=["current", "all", "collection"])
    ap.add_argument("--quality", default="best", choices=list(QUALITY_TARGET))
    ap.add_argument("--media", default="video", choices=["video", "audio-original", "audio-mp3", "audio-m4a"])
    ap.add_argument("--audio-quality", default="auto", choices=["auto", "320", "256", "192", "128"])
    ap.add_argument("--danmaku", default="off", choices=["off", "ass", "xml"])
    ap.add_argument("--subtitles", default="off", choices=["off", "srt"])
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--jobs-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    return ap


def setup_bili(bili_root: Path):
    """把 Bili23 的模块目录挂进 sys.path。

    注意：不要创建 QCoreApplication，也不要设置应用名。Bili23 的 config.py
    在导入时用 QStandardPaths.AppDataLocation + "Bili23 Downloader" 定位
    config.json；只有 Qt 应用名为空时 AppDataLocation 才是纯 %APPDATA%，
    与 Bili23 GUI 的实际配置路径一致。
    """
    # 嵌入式 Python 不执行 site 机制，pywin32.pth 里的目录要手动挂上
    for rel in ("site-packages", "site-packages/win32", "site-packages/win32/lib",
                "site-packages/Pythonwin", "site-packages/pywin32_system32", "script"):
        path = str(bili_root / rel)
        if path not in sys.path:
            sys.path.insert(0, path)
    pywin32_dlls = str(bili_root / "site-packages" / "pywin32_system32")
    os.environ["PATH"] = pywin32_dlls + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        global _DLL_HANDLE
        _DLL_HANDLE = os.add_dll_directory(pywin32_dlls)


def _cap_collection(leaves):
    return list(leaves[:MAX_COLLECTION_LEAVES])


def _video_leaf(base, data, page=None, episode=None, collection_title=""):
    source = page or episode or data
    arc = (episode or {}).get("arc") or {}
    leaf = {
        **base,
        "aid": source.get("aid", episode.get("aid", data.get("aid", 0)) if episode else data.get("aid", 0)),
        "bvid": source.get("bvid", episode.get("bvid", data.get("bvid", "")) if episode else data.get("bvid", "")),
        "cid": source.get("cid", data.get("cid", 0)),
        "title": source.get("part") or source.get("title") or data.get("title", ""),
        "part_number": source.get("page", 0) or 1,
        "duration": source.get("duration", arc.get("duration", data.get("duration", 0))),
        "pubtime": source.get("ctime", arc.get("pubdate", data.get("pubdate", 0))),
        "attribute": 1 | 256,
    }
    if episode:
        leaf["parent_title"] = episode.get("title") or leaf["title"]
        leaf["uploader"] = (episode.get("owner") or {}).get("name") or base.get("uploader", "")
    if collection_title:
        leaf["_collection_title"] = collection_title
        leaf["_collection"] = True
    return leaf


def _video_collection_leaves(data):
    season = data.get("ugc_season") or {}
    collection_title = season.get("title") or data.get("title") or ""
    base = {
        "bvid": data.get("bvid", ""),
        "aid": data.get("aid", 0),
        "uploader": (data.get("owner") or {}).get("name", ""),
        "parent_title": data.get("title", ""),
    }
    leaves = []
    for section in season.get("sections") or []:
        for episode in section.get("episodes") or []:
            leaves.append(_video_leaf(base, data, episode=episode,
                                      collection_title=collection_title))
    return _cap_collection(leaves), collection_title


def video_leaves(info: dict, part: str, page_from_url: int):
    """把 view 接口数据整理成待下载的单集列表。"""
    data = info["data"]
    base = {
        "bvid": data.get("bvid", ""),
        "aid": data.get("aid", 0),
        "uploader": (data.get("owner") or {}).get("name", ""),
        "parent_title": data.get("title", ""),
    }
    if part == "collection":
        leaves, collection_title = _video_collection_leaves(data)
        if leaves:
            return leaves
        # A regular multi-part video is the documented collection fallback.
        pages = data.get("pages") or []
        if len(pages) > 1:
            leaves = [_video_leaf(base, data, page=page) for page in pages]
        else:
            leaves = [_video_leaf(base, data)]
        if leaves:
            leaves[0]["_collection_fallback"] = True
        return leaves

    pages = data.get("pages") or []
    leaves = []
    if part == "all" and len(pages) > 1:
        for page in pages:
            leaves.append(_video_leaf(base, data, page=page))
    else:
        leaves.append(_video_leaf(base, data, page={
            "cid": data.get("cid", 0),
            "part": data.get("title", ""),
            "page": page_from_url or 1,
            "duration": data.get("duration", 0),
            "ctime": data.get("pubdate", 0),
        }))
    return leaves


def parse_url(url: str, part: str):
    """返回 (leaves, parent_title)。"""
    from util.parse.worker import WorkerBase

    worker = WorkerBase()
    parser_type = worker.get_parser_type(url)

    if parser_type == "video":
        from util.parse.parser.video import VideoParser
        info = VideoParser().parse(url, 1, get_info_data=True)
        page_match = re.search(r"[?&]p=(\d+)", url)
        page_from_url = int(page_match.group(1)) if page_match else 1
        leaves = video_leaves(info, part, page_from_url)
        if part == "collection" and leaves and leaves[0].get("_collection"):
            return leaves, leaves[0].get("_collection_title") or (info["data"].get("title") or "")
        return leaves, (info["data"].get("title") or "")

    if parser_type in ("bangumi", "cheese"):
        if parser_type == "bangumi":
            from util.parse.parser.bangumi import BangumiParser as P
            from util.parse.episode.bangumi import BangumiEpisodeParser as EP
        else:
            from util.parse.parser.cheese import CheeseParser as P
            from util.parse.episode.cheese import CheeseEpisodeParser as EP

        parser = P()
        parser.url = url
        # 复刻 parse() 的取参分支，但不把解析结果推给 GUI 列表
        kind = parser.find_str(r"ep|ss|md", url)
        if kind == "ep":
            param = parser.get_ep_id()
        elif kind == "ss":
            param = parser.get_season_id()
        else:
            param = parser.get_media_id()
        parser.get_bangumi_info(param)
        node = EP(parser.info_data.copy(), parser.get_category_name()).parse(update_episode_list=False)

        all_leaves = [leaf.to_dict() for leaf in node.get_all_leaves()]
        result = parser.info_data.get("result") or {}
        parent = result.get("series", {}).get("series_title") or result.get("season_title") or ""
        if part == "collection":
            for leaf in all_leaves:
                leaf["_collection"] = True
                leaf["_collection_title"] = result.get("season_title") or parent
            return _cap_collection(all_leaves), result.get("season_title") or parent

        current_ep = None
        ep_match = re.search(r"ep(\d+)", url)
        if ep_match:
            current_ep = int(ep_match.group(1))
        elif isinstance(result, dict):
            current_ep = result.get("current_ep_id")

        chosen = None
        for leaf in all_leaves:
            if current_ep is not None and int(leaf.get("ep_id") or 0) == int(current_ep):
                chosen = leaf
                break
        if chosen is None:
            chosen = all_leaves[0] if all_leaves else None
        if chosen is None:
            raise RuntimeError("没有可下载的剧集。")
        return [chosen], parent

    raise RuntimeError("暂不支持该类型的链接，请使用“送入 Bili23”。")


def make_task(leaf: dict, parent_title: str, media: str = "video"):
    from util.download.task.info import TaskInfo
    from util.common.enum import DownloadType

    task = TaskInfo()
    task.Basic.task_id = uuid.uuid4().hex[:12]
    ep = task.Episode
    ep.attribute = int(leaf.get("attribute") or 1)
    ep.aid = int(leaf.get("aid") or 0)
    ep.bvid = leaf.get("bvid") or ""
    ep.cid = int(leaf.get("cid") or 0)
    ep.sid = int(leaf.get("sid") or 0)
    ep.ep_id = int(leaf.get("ep_id") or 0)
    ep.leaf_title = leaf.get("title") or ""
    ep.parent_title = parent_title or ""
    ep.show_title = ep.parent_title or ep.leaf_title
    ep.number = leaf.get("number") or ""
    ep.part_number = int(leaf.get("part_number") or 0)
    ep.duration = int(leaf.get("duration") or 0)
    ep.pubtime = int(leaf.get("pubtime") or 0)
    ep.url = leaf.get("url") or ""
    uploader = leaf.get("uploader_info") or {}
    ep.uploader = leaf.get("uploader") or uploader.get("uploader") or ""
    ep.uploader_uid = int(uploader.get("uploader_uid") or 0)

    if media not in ("video", "audio-original", "audio-mp3", "audio-m4a"):
        raise ValueError("不支持的媒体选项。")
    task.Download.type = (DownloadType.VIDEO | DownloadType.AUDIO) if media == "video" else DownloadType.AUDIO
    task.Download.video_codec_id = 20       # 20 = 按配置的编码优先级自动选择
    task.Download.audio_quality_id = 30300  # 按配置的音频优先级自动选择
    return task


def pick_quality(available, target):
    if not available:
        return 0
    below = [q for q in available if q <= target]
    return max(below) if below else min(available)


def original_audio_source(dash: dict):
    # Defined rendition preference, not a cross-codec/channel bitrate ranking.
    flac = (dash.get("flac") or {}).get("audio")
    dolby = (dash.get("dolby") or {}).get("audio") or []
    ordinary = dash.get("audio") or []
    candidates = ([flac] if isinstance(flac, dict) else []) or dolby or ordinary
    candidates = [p for p in candidates if isinstance(p, dict)]
    if not candidates:
        return None
    def number(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    tier = max(number(p.get("id")) for p in candidates)
    candidates = [p for p in candidates if number(p.get("id")) == tier]
    def rendition(p):
        return (str(p.get("codecs", "")).lower(), str(p.get("profile", "")),
                str(p.get("channels", "")), str(p.get("channel_layout", "")))
    same = [p for p in candidates if rendition(p) == rendition(candidates[0])]
    return max(same, key=lambda p: number(p.get("bandwidth")))


def resolve_entries(task, quality: str, media: str = "video"):
    """playurl 取流并解析出下载条目列表。"""
    from util.download.downloader.parse_worker import ParseWorker
    from util.download.parse.video_info import VideoInfoParser
    from util.download.parse.audio_info import AudioInfoParser
    from util.common.enum import MediaType

    pw = ParseWorker(task)
    pw.get_info()
    if not pw.info_data:
        raise RuntimeError("无法获取下载链接。")

    media_type = task.Download.media_type
    entries = []

    if task.Download.type & 1:  # DownloadType.VIDEO
        vip = VideoInfoParser(pw.info_data, task)
        available = vip.get_available_quality_list()
        task.Download.video_quality_id = pick_quality(available, QUALITY_TARGET.get(quality, 999))
        entries.extend(vip.parse_info())

    if task.Download.type & 2:  # DownloadType.AUDIO
        if media == "audio-original" and media_type == MediaType.DASH:
            from util.download.parse.query_worker import QueryWorker
            source = original_audio_source(pw.info_data.get("dash") or {})
            if source:
                task.Download.audio_quality_id = int(source.get("id") or 0)
                entries.append({**QueryWorker(source).query_dash_url(), "type": "audio", "file_key": "audio",
                                "file_name": "audio_%s.source" % task.Basic.task_id})
        else:
            aip = AudioInfoParser(pw.info_data, task)
            entries.extend(aip.parse_info())

    if media != "video" and not entries and media_type in (MediaType.MP4, MediaType.FLV):
        # Old muxed streams have no separate audio. Use only the same authorized
        # playurl response; never change login/entitlement checks or request privileges.
        vip = VideoInfoParser(pw.info_data, task)
        available = vip.get_available_quality_list()
        task.Download.video_quality_id = pick_quality(available, QUALITY_TARGET[quality])
        entries.extend(vip.parse_info())

    if not entries:
        raise RuntimeError("没有可下载的流。")
    return entries, media_type


def transfer_workers():
    """Reuse Bili23's per-file concurrency without changing its settings."""
    try:
        from util.common.config import config
        value = config.get(config.download_thread)
        return max(1, min(10, value)) if type(value) is int else 4
    except (ImportError, AttributeError):
        return 4


def download_entry(entry, work: Path, job: Job, progress):
    target = work / entry["file_name"]
    total = int(entry.get("file_size") or 0)
    workers = transfer_workers()
    if total >= RANGE_SIZE * 2 and workers > 1 and not target.exists():
        reported = [0]
        def range_progress(delta):
            reported[0] += delta
            progress(delta)
        try:
            return download_entry_parallel(entry, work, job, range_progress, workers)
        except JobCanceled:
            raise
        except Exception:
            # Range support and connection limits differ between CDN nodes.
            # Discard the private staging file and retain the ordinary path.
            progress(-reported[0])
    return download_entry_serial(entry, work, job, progress)


def download_entry_parallel(entry, work: Path, job: Job, progress, workers):
    from util.network.request import get_client

    total = int(entry["file_size"])
    target = work / entry["file_name"]
    staging = work / (".veylo-range-" + uuid.uuid4().hex + ".part")
    stopped = Event()
    events = SimpleQueue()
    client = get_client()
    ranges = [(start, min(start + RANGE_SIZE, total) - 1) for start in range(0, total, RANGE_SIZE)]
    # Schedule a bounded number of workers even for multi-hour media.
    cursor = iter(ranges)
    cursor_lock = Lock()

    def worker():
        with open(staging, "r+b") as handle:
            while not stopped.is_set():
                with cursor_lock:
                    bounds = next(cursor, None)
                if bounds is None:
                    return
                start, end = bounds
                offset = start
                for attempt in range(RETRIES):
                    if stopped.is_set():
                        return
                    try:
                        headers = {"User-Agent": UA, "Referer": REFERER, "Range": f"bytes={offset}-{end}"}
                        with client.stream("GET", entry["url"], headers=headers, timeout=10) as response:
                            response.raise_for_status()
                            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                            if response.status_code != 206 or not match or tuple(map(int, match.groups())) != (offset, end, total):
                                raise RangeUnavailable("服务器未返回请求的媒体分段。")
                            handle.seek(offset)
                            for chunk in response.iter_bytes(CHUNK):
                                if stopped.is_set():
                                    return
                                if len(chunk) > end - offset + 1:
                                    raise ValueError("媒体分段大小无效。")
                                handle.write(chunk)
                                offset += len(chunk)
                                events.put(len(chunk))
                        if offset != end + 1:
                            raise ValueError("媒体分段不完整。")
                        break
                    except RangeUnavailable:
                        raise
                    except Exception:
                        if attempt == RETRIES - 1:
                            raise
                        if stopped.wait(.5 * (attempt + 1)):
                            return

    job.start_transfer()
    try:
        with open(staging, "wb") as handle:
            handle.truncate(total)
        with ThreadPoolExecutor(max_workers=min(workers, len(ranges))) as pool:
            futures = [pool.submit(worker) for _ in range(min(workers, len(ranges)))]
            try:
                while True:
                    for future in futures:
                        if future.done():
                            future.result()
                    delta = 0
                    try:
                        delta = events.get(timeout=.1)
                        while True:
                            delta += events.get_nowait()
                    except Empty:
                        pass
                    # Only this thread touches the job file and progress closure.
                    if delta:
                        job.transfer_progress(delta)
                    progress(delta)
                    if all(future.done() for future in futures) and events.empty():
                        break
            finally:
                stopped.set()
            for future in futures:
                future.result()
        os.replace(staging, target)
    finally:
        stopped.set()
        staging.unlink(missing_ok=True)
        job.stop_transfer()


def download_entry_serial(entry, work: Path, job: Job, progress):
    import httpx
    from util.network.request import get_client

    target = work / entry["file_name"]
    done = target.stat().st_size if target.exists() else 0
    total = int(entry.get("file_size") or 0)
    client = get_client()

    for attempt in range(1, RETRIES + 1):
        try:
            headers = {"User-Agent": UA, "Referer": REFERER}
            if done:
                headers["Range"] = "bytes=%d-" % done
            job.start_transfer()
            try:
                with client.stream("GET", entry["url"], headers=headers, timeout=60) as response:
                    response.raise_for_status()
                    mode = "ab" if (done and response.status_code == 206) else "wb"
                    if mode == "wb":
                        done = 0
                    with open(target, mode) as handle:
                        for chunk in response.iter_bytes(CHUNK):
                            handle.write(chunk)
                            done += len(chunk)
                            job.transfer_progress(len(chunk))
                            progress(len(chunk))
            finally:
                job.stop_transfer()
            if total and done < total:
                raise RuntimeError("下载的数据不完整（%d/%d）" % (done, total))
            return
        except JobCanceled:
            raise
        except Exception:
            if attempt >= RETRIES:
                raise
            time.sleep(2 * attempt)


def run_ffmpeg(args, cwd: Path):
    proc = subprocess.run(args, cwd=str(cwd), capture_output=True, timeout=3600)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace")[-400:]
        raise RuntimeError("FFmpeg 合并失败：" + tail)


def configured_audio_ffmpeg():
    """Share the explicit, local FluentYTDL root with the YouTube/X bridge."""
    try:
        data = json.loads(FLUENT_PATHS.read_text(encoding="utf-8-sig"))
        raw = data.get("fluent_root") if isinstance(data, dict) else None
        if not isinstance(raw, str) or not raw or any(ord(char) < 32 for char in raw):
            return None
        root = Path(raw)
        if not root.is_absolute() or root.drive.startswith("\\\\"):
            return None
        tool = root / "bin/ffmpeg/ffmpeg.exe"
        # Reject linked roots, child directories and executable files before discovery succeeds.
        for part in (*reversed(tool.parents), tool):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return None
        return str(tool) if tool.is_file() else None
    except (OSError, ValueError, TypeError):
        return None


def audio_ffmpeg(bili_root: Path):
    """Reuse an installed full FFmpeg for codecs absent from Bili23's small bundle.

    An explicit native/fluent_paths.json takes precedence over bounded desktop
    discovery. No third-party settings are changed and no binary is installed.
    """
    configured = configured_audio_ffmpeg()
    if configured:
        return configured, True
    desktop = Path.home() / "Desktop"
    legacy = desktop / "FluentYTDL-3.7.6-win64-full"
    candidates = [legacy]
    category = desktop / "Applications"
    if category.is_dir():
        matches = [p for p in category.glob("FluentYTDL-*-win64-full")
                   if (p / "bin/ffmpeg/ffmpeg.exe").is_file()]
        if len(matches) == 1:
            candidates.append(matches[0])
    for root in candidates:
        tool = root / "bin/ffmpeg/ffmpeg.exe"
        if tool.is_file():
            return str(tool), True
    return str(bili_root / "bundle/ffmpeg.exe"), False


def original_tools(bili_root: Path):
    tool, full_audio = audio_ffmpeg(bili_root)
    probe = Path(tool).with_name("ffprobe.exe")
    if not full_audio or not Path(tool).is_file() or not probe.is_file():
        raise OriginalToolsUnavailable(ORIGINAL_TOOLS_ERROR)
    return str(tool), str(probe)


def original_stream(probe: str, source: Path):
    result = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_streams", "-show_data_hash", "sha256",
                             "-of", "json", str(source)], capture_output=True, timeout=30)
    try:
        streams = json.loads(result.stdout).get("streams") or []
        stream = streams[0]
        if result.returncode or not stream.get("codec_name") or not stream.get("sample_rate") or not stream.get("channels"):
            raise ValueError("unidentified audio")
        return stream
    except (ValueError, IndexError, TypeError, KeyError):
        raise OriginalCopyError(ORIGINAL_COPY_ERROR)


def original_inputs(entries):
    audio = next((e for e in entries if e["file_key"] == "audio"), None)
    inputs = [audio] if audio else sorted((e for e in entries if e["file_key"].startswith("video_part_")),
                                         key=lambda e: int(e["file_key"].rsplit("_", 1)[1]))
    if not inputs or len(inputs) > 100:
        raise OriginalCopyError(ORIGINAL_COPY_ERROR)
    return inputs


def original_extension(stream):
    return {"aac": ".m4a", "alac": ".m4a", "opus": ".opus", "mp3": ".mp3", "flac": ".flac",
            "vorbis": ".ogg", "ac3": ".ac3", "eac3": ".eac3"}.get(stream["codec_name"], ".mka")


def original_signature(stream):
    return tuple(stream.get(key) for key in ("codec_name", "profile", "sample_fmt", "sample_rate", "channels",
                                             "channel_layout", "bits_per_raw_sample", "extradata_hash"))


def copy_original(entries, work: Path, output: Path, bili_root: Path):
    tool, probe = original_tools(bili_root)
    inputs = [work / e["file_name"] for e in original_inputs(entries)]
    streams = [original_stream(probe, source) for source in inputs]
    if any(original_signature(s) != original_signature(streams[0]) for s in streams[1:]) or output.suffix != original_extension(streams[0]):
        raise OriginalCopyError(ORIGINAL_COPY_ERROR)
    try:
        # Only our private partials are overwritten/removed; Windows rename will
        # reject a final filename occupied by a concurrent job.
        with tempfile.TemporaryDirectory(prefix=".veylo-original-%s-" % work.name[:-5] if work.name.endswith(".work") else ".veylo-original-", dir=output.parent) as temp:
            directory = Path(temp)
            partial = directory / ("complete" + output.suffix)
            if len(inputs) == 1:
                run_ffmpeg([tool, "-nostdin", "-y", "-i", str(inputs[0]), "-map", "0:a:0", "-vn", "-c:a", "copy", str(partial)], work)
            else:
                parts = []
                for index, source in enumerate(inputs):
                    part = directory / ("part-%03d" % index + output.suffix)
                    run_ffmpeg([tool, "-nostdin", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "copy", str(part)], work)
                    parts.append(part)
                listing = directory / "parts.ffconcat"
                listing.write_text("ffconcat version 1.0\n" + "".join("file '%s'\n" % part.name for part in parts), encoding="utf-8")
                run_ffmpeg([tool, "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", str(listing), "-map", "0:a:0", "-vn", "-c:a", "copy", str(partial)], work)
            if original_signature(original_stream(probe, partial)) != original_signature(streams[0]):
                raise OriginalCopyError(ORIGINAL_COPY_ERROR)
            partial.rename(output)
    except Exception as exc:
        raise OriginalCopyError(ORIGINAL_COPY_ERROR) from exc


def merge(task, entries, media_type, work: Path, output: Path, bili_root: Path, media: str = "video", audio_quality: str = "auto"):
    from util.common.enum import MediaType

    audio_quality = normalized_audio_quality(media, audio_quality)
    ffmpeg = str(bili_root / "bundle" / "ffmpeg.exe")
    output.parent.mkdir(parents=True, exist_ok=True)

    if media not in ("video", "audio-original", "audio-mp3", "audio-m4a"):
        raise ValueError("不支持的媒体选项。")
    if media == "audio-original":
        copy_original(entries, work, output, bili_root)
        return
    by_key = {e["file_key"]: e for e in entries}
    video = by_key.get("video")
    audio = by_key.get("audio")

    if media != "video":
        if audio:
            inputs = [audio]
        else:
            inputs = sorted((e for e in entries if e["file_key"].startswith("video_part_")),
                            key=lambda e: int(e["file_key"].rsplit("_", 1)[1]))
            if not inputs:
                raise RuntimeError("没有可提取的音频流。")
        tool, full_audio = audio_ffmpeg(bili_root)
        if audio_quality != "auto" and not full_audio:
            raise AudioEncoderUnavailable(AUDIO_ENCODER_ERROR)
        cmd = [tool, "-nostdin", "-y"]
        for entry in inputs:
            cmd += ["-i", str(work / entry["file_name"])]
        if len(inputs) > 1:
            labels = "".join("[%d:a:0]" % index for index in range(len(inputs)))
            cmd += ["-filter_complex", labels + "concat=n=%d:v=0:a=1[outa]" % len(inputs), "-map", "[outa]"]
        else:
            cmd += ["-map", "0:a:0"]
        cmd += ["-vn"]
        if audio_quality != "auto":
            cmd += ["-c:a", "libmp3lame" if media == "audio-mp3" else "aac", "-b:a", audio_quality + "k"]
            if media == "audio-m4a":
                cmd += ["-movflags", "+faststart"]
        elif media == "audio-mp3":
            cmd += ["-c:a", "libmp3lame", "-q:a", "0"]
        elif full_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        else:
            # AAC streams can be repackaged by the small Bili23 build without
            # an AAC encoder. Other codecs fail clearly rather than mislabeling.
            cmd += ["-c:a", "copy", "-movflags", "+faststart"]
        cmd.append(str(output))
        try:
            run_ffmpeg(cmd, work)
        except Exception:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return

    if media_type == MediaType.DASH:
        cmd = [ffmpeg, "-y", "-i", str(work / video["file_name"])]
        if audio:
            cmd += ["-i", str(work / audio["file_name"])]
        cmd += ["-c", "copy", "-movflags", "+faststart", str(output)]
        run_ffmpeg(cmd, work)
        return

    if media_type == MediaType.M4A:
        os.replace(work / audio["file_name"], output)
        return

    if media_type in (MediaType.MP4, MediaType.FLV):
        parts = sorted((e for e in entries if e["file_key"].startswith("video_part_")),
                       key=lambda e: e["file_key"])
        if len(parts) == 1:
            os.replace(work / parts[0]["file_name"], output)
            return
        raise RuntimeError("该视频是旧格式多段流，请改用“送入 Bili23”下载。")

    raise RuntimeError("不支持的媒体类型。")


def completed_files(output_dir: Path, paths) -> list:
    """Only regular, existing local files strictly below the selected directory."""
    root = output_dir.resolve(strict=True)
    result = []
    if not root.is_dir() or str(root).startswith("\\\\"):
        return result
    for raw in paths:
        if not isinstance(raw, (str, Path)) or any(ord(c) < 32 or ord(c) == 127 for c in str(raw)):
            continue
        try:
            candidate = Path(raw)
            path = candidate.resolve(strict=True)
            if not candidate.is_absolute() or path == root or not path.is_relative_to(root) or not path.is_file():
                continue
            if any(p.is_symlink() or (getattr(p, "is_junction", lambda: False)()) for p in [candidate, *candidate.parents]):
                continue
            text = str(path)
            if text not in result:
                result.append(text)
            if len(result) == 20:
                break
        except (OSError, ValueError):
            continue
    return result


def checked_output_directory(path: Path, root: Path) -> Path:
    """Validate against the original selected root before creating or writing output."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise RuntimeError("输出文件必须位于任务保存目录内。")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("输出目录不能包含链接或重解析点。")
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("输出文件必须位于任务保存目录内。")
    return path


def output_path_for(task, output_dir: Path, ext: str, media: str = "video", audio_quality: str = "auto") -> Path:
    audio_quality = normalized_audio_quality(media, audio_quality)
    rel = None
    try:
        from util.format.file_name import FileNameFormatter
        fmt = FileNameFormatter()
        fmt.set_variable_data(task)
        rel = fmt.format()
    except Exception:
        rel = None

    if rel and (Path(rel).is_absolute() or ".." in Path(rel).parts
                or any(ord(c) < 32 or ord(c) == 127 for c in str(rel))):
        rel = None
    if rel:
        path = output_dir / rel
        if media != "video" or path.suffix.lower() not in (".mp4", ".mkv", ".flv", ".m4a"):
            path = path.with_suffix(ext)
    else:
        ep = task.Episode
        base = ep.leaf_title or ep.show_title or (ep.bvid or "video")
        if ep.parent_title and ep.parent_title != ep.leaf_title:
            base = "%s - %s" % (ep.parent_title, ep.leaf_title)
        if ep.part_number > 1:
            base = "%s (P%d)" % (base, ep.part_number)
        suffix = " [%s]" % ep.bvid if ep.bvid else ""
        path = output_dir / (sanitize(base) + suffix + ext)

    if media == "audio-original":
        path = path.with_name("%s [original]%s" % (path.stem, path.suffix))
    if audio_quality != "auto":
        path = path.with_name("%s [%skbps]%s" % (path.stem, audio_quality, path.suffix))
    if not path.resolve().is_relative_to(output_dir.resolve()):
        raise RuntimeError("输出文件必须位于任务保存目录内。")
    checked_output_directory(path.parent, output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_output_directory(path.parent, output_dir)
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 100):
        candidate = path.with_name("%s (%d)%s" % (stem, index, suffix))
        if not candidate.exists():
            return candidate
    return path.with_name("%s-%d%s" % (stem, int(time.time()), suffix))


def fetch_danmaku(task):
    from util.parse.additional.danmaku import DanmakuParser
    return DanmakuParser(task)._get_all_protobuf_parts()


def fetch_subtitles(task):
    from util.parse.additional.player import PlayerInfoParser
    from util.parse.additional.subtitles import SubtitlesParser
    player_data = PlayerInfoParser(task).get_data()
    parser = SubtitlesParser(task)
    result = []
    for entry in (player_data.get("subtitle", {}).get("subtitles", []) or []):
        language = entry.get("lan") or entry.get("language") or "und"
        url = entry.get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://api.bilibili.com" + url
        if not url:
            continue
        data = parser._get_subtitles_data(url)
        if data:
            result.append({"language": language, "data": data})
    return result


def _write_extra(path: Path, contents: str, job_id=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    tag = str(job_id or uuid.uuid4().hex)
    partial = path.with_name(".veylo-partial-%s-%s" % (tag, uuid.uuid4().hex))
    try:
        partial.write_text(contents, encoding="utf-8", newline="\n")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def write_danmaku(output: Path, mode: str, entries, cid, job_id="") -> Path:
    if mode == "ass":
        path = output.with_name(output.stem + ".danmaku.ass")
        return _write_extra(path, danmaku_ass(entries, output.stem), job_id)
    if mode == "xml":
        path = output.with_name(output.stem + ".danmaku.xml")
        return _write_extra(path, danmaku_xml(entries, cid), job_id)
    raise ValueError("不支持的弹幕格式。")


def write_subtitle(output: Path, language: str, data: dict, job_id="") -> Path:
    language = safe_language(language)
    path = output.with_name(output.stem + ".%s.srt" % language)
    return _write_extra(path, subtitle_srt(data), job_id)


def download_extras(task, output: Path, danmaku="off", subtitles="off", danmaku_fetcher=None,
                    subtitle_fetcher=None, job_id=""):
    """Write optional files; return each feature's isolated error instead of failing media."""
    paths = []
    errors = set()
    if danmaku != "off":
        try:
            entries = (danmaku_fetcher or fetch_danmaku)(task)
            paths.append(write_danmaku(output, danmaku, entries or [], task.Episode.cid, job_id))
        except JobCanceled:
            raise
        except Exception:
            errors.add("danmaku")
    if subtitles != "off":
        try:
            entries = (subtitle_fetcher or fetch_subtitles)(task)
        except JobCanceled:
            raise
        except Exception:
            entries = []
            errors.add("subtitles")
        for entry in entries or []:
            try:
                language = entry.get("language") or entry.get("lan") or "und"
                data = entry.get("data") or entry
                if subtitle_srt(data):
                    paths.append(write_subtitle(output, language, data, job_id))
            except JobCanceled:
                raise
            except Exception:
                errors.add("subtitles")
    return paths, errors


def validate_driver_options(args):
    media = getattr(args, "media", "video")
    quality = getattr(args, "quality", "best")
    part = getattr(args, "part", "current")
    danmaku = getattr(args, "danmaku", "off")
    subtitles = getattr(args, "subtitles", "off")
    if media not in ("video", "audio-original", "audio-mp3", "audio-m4a"):
        raise ValueError("不支持的媒体选项：media。")
    if quality not in QUALITY_TARGET:
        raise ValueError("不支持的下载选项：quality。")
    if part not in ("current", "all", "collection"):
        raise ValueError("不支持的下载选项：part。")
    if danmaku not in ("off", "ass", "xml"):
        raise ValueError("不支持的下载选项：danmaku。")
    if subtitles not in ("off", "srt"):
        raise ValueError("不支持的下载选项：subtitles。")
    return media, quality, part, danmaku, subtitles


def _job_files(output_dir: Path, all_files):
    # Media entries always occupy the public cap before optional sidecars.
    ordered = sorted(all_files, key=lambda path: Path(path).suffix.lower() in (".ass", ".xml", ".srt"))
    return completed_files(output_dir, ordered)


def _job_count(output_dir: Path, all_files):
    return completed_file_count(output_dir, all_files)


def run(args, job: Job):
    bili_root = Path(args.bili_root)
    output_dir = Path(args.output_dir)
    jobs_dir = Path(args.jobs_dir)
    media, quality, part, danmaku, subtitles = validate_driver_options(args)
    audio_quality = normalized_audio_quality(media, getattr(args, "audio_quality", "auto"))
    job_id = args.job_id
    check_canceled(jobs_dir, job_id)
    if media == "audio-original":
        original_tools(bili_root)
    if media != "video" and audio_quality != "auto" and not audio_ffmpeg(bili_root)[1]:
        raise AudioEncoderUnavailable(AUDIO_ENCODER_ERROR)
    work = jobs_dir / (job_id + ".work")
    work.mkdir(parents=True, exist_ok=True)

    setup_bili(bili_root)

    from util.common.enum import MediaType

    check_canceled(jobs_dir, job_id)
    leaves, parent_title = parse_url(args.url, part)
    total_episodes = len(leaves)
    if not total_episodes:
        raise RuntimeError("没有可下载的剧集。")
    if total_episodes > MAX_COLLECTION_LEAVES:
        leaves = _cap_collection(leaves)
        total_episodes = len(leaves)
    collection_mode = part == "collection" and any(leaf.get("_collection") for leaf in leaves)
    leaf_output_dir = output_dir / sanitize(parent_title)[:80] if collection_mode else output_dir
    checked_output_directory(leaf_output_dir, output_dir)
    leaf_output_dir.mkdir(parents=True, exist_ok=True)
    checked_output_directory(leaf_output_dir, output_dir)
    title = re.sub(r"[\x00-\x1f\x7f]", "", parent_title or leaves[0].get("title") or "")[:512]
    files = completed_files(output_dir, job.data.get("files") or [])
    all_files = list(files)
    job.save(state="downloading", percent=0.0, totalEpisodes=total_episodes,
             platform="bilibili", media=media, audioQuality=audio_quality,
             danmaku=danmaku, biliSubtitles=subtitles, title=title, files=files,
             fileCount=_job_count(output_dir, all_files))
    if part == "collection" and any(leaf.get("_collection_fallback") for leaf in leaves):
        add_warning(job, WARN_FALLBACK)

    succeeded = 0
    last_error = None
    danmaku_fetcher = getattr(args, "danmaku_fetcher", None)
    subtitle_fetcher = getattr(args, "subtitle_fetcher", None)
    for index, leaf in enumerate(leaves):
        check_canceled(jobs_dir, job_id)
        task = None
        entries = []
        try:
            task = make_task(leaf, parent_title, media)
            entries, media_type = resolve_entries(task, quality, media)

            total_size = sum(int(e.get("file_size") or 0) for e in entries) or 1
            done_size = [0]

            def progress(delta, _base=index, _done=done_size, _total=total_size):
                check_canceled(jobs_dir, job_id)
                _done[0] += delta
                episode_fraction = min(1.0, _done[0] / _total)
                percent = (_base + episode_fraction) / total_episodes * 100.0
                job.save(force=False, percent=round(min(99.0, percent), 1))

            for entry in entries:
                check_canceled(jobs_dir, job_id)
                download_entry(entry, work, job, progress)

            ext = {"audio-mp3": ".mp3", "audio-m4a": ".m4a"}.get(media)
            if media == "audio-original":
                _, probe = original_tools(bili_root)
                first = original_inputs(entries)[0]
                ext = original_extension(original_stream(probe, work / first["file_name"]))
            if ext is None:
                ext = ".m4a" if media_type == MediaType.M4A else ".mp4"
            output = output_path_for(task, leaf_output_dir, ext, media, audio_quality)
            checked_output_directory(output.parent, output_dir)
            if media == "audio-original":
                # copy_original already stages atomically and verifies source packet compatibility.
                merge(task, entries, media_type, work, output, bili_root, media, audio_quality)
            else:
                with tempfile.TemporaryDirectory(prefix=".veylo-original-%s-" % job_id, dir=output.parent) as temp:
                    partial = Path(temp) / ("complete" + output.suffix)
                    merge(task, entries, media_type, work, partial, bili_root, media, audio_quality)
                    check_canceled(jobs_dir, job_id)
                    partial.rename(output)
            if not output.is_file() or not output.resolve().is_relative_to(output_dir.resolve()):
                raise RuntimeError("没有生成有效的输出文件。")
            all_files.append(str(output))
            succeeded += 1
            files = _job_files(output_dir, all_files)
            job.save(state="downloading", percent=round(min(99.0, (index + 0.95) / total_episodes * 100.0), 1),
                     files=files, fileCount=_job_count(output_dir, all_files))
            check_canceled(jobs_dir, job_id)
            extra_paths, extra_errors = download_extras(task, output, danmaku, subtitles,
                                                        danmaku_fetcher, subtitle_fetcher, job_id)
            all_files.extend(str(path) for path in extra_paths)
            if "danmaku" in extra_errors:
                add_warning(job, WARN_DANMAKU)
            if "subtitles" in extra_errors:
                add_warning(job, WARN_SUBTITLE)

            files = _job_files(output_dir, all_files)
            for entry in entries:
                try:
                    (work / entry["file_name"]).unlink()
                except OSError:
                    pass
            job.save(state="downloading", percent=round(min(99.0, (index + 1) / total_episodes * 100.0), 1),
                     files=files, fileCount=_job_count(output_dir, all_files))
            check_canceled(jobs_dir, job_id)
        except JobCanceled:
            raise
        except Exception as exc:
            last_error = exc
            if succeeded or total_episodes > 1:
                add_warning(job, WARN_PARTIAL)
                for entry in entries:
                    try:
                        (work / entry["file_name"]).unlink()
                    except OSError:
                        pass
                continue
            raise

    if not succeeded:
        raise last_error or RuntimeError("没有成功下载的条目。")

    try:
        work.rmdir()
    except OSError:
        pass

    check_canceled(jobs_dir, job_id)
    job.save(state="completed", percent=100.0, error="", outputDir=str(output_dir),
             title=title, media=media, audioQuality=audio_quality,
             danmaku=danmaku, biliSubtitles=subtitles, files=_job_files(output_dir, all_files),
             fileCount=_job_count(output_dir, all_files))


def main():
    args = build_argparser().parse_args()
    jobs_dir = Path(args.jobs_dir)
    job = Job(jobs_dir / (args.job_id + ".json"))
    try:
        run(args, job)
    except Exception as exc:
        try:
            (jobs_dir / (args.job_id + ".log")).write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        try:
            if isinstance(exc, JobCanceled):
                work = jobs_dir / (args.job_id + ".work")
                output_dir = Path(args.output_dir)
                cleanup_canceled(work, output_dir, args.job_id)
                existing = job.data.get("files") or []
                file_count = job.data.get("fileCount")
                if type(file_count) is not int or not 0 <= file_count <= 100000:
                    file_count = completed_file_count(output_dir, existing)
                job.save(state="canceled", error="已取消下载。", files=completed_files(output_dir, existing),
                         fileCount=file_count)
                try:
                    cancel_marker(jobs_dir, args.job_id).unlink()
                except OSError:
                    pass
            else:
                job.save(state="failed", error=friendly(exc))
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
