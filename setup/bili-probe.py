"""Check the bridge's Bili23 imports against a disposable profile."""
import os
from pathlib import Path
import runpy
import sys


def main():
    driver_path, vendor, profile = map(Path, sys.argv[1:])
    for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        os.environ[key] = str(profile)
    driver = runpy.run_path(str(driver_path))
    driver["setup_bili"](vendor)
    from PySide6.QtCore import QStandardPaths

    # Qt resolves Windows known folders independently of the environment.
    # The installer removes this directory after the probe's database handles close.
    QStandardPaths.writableLocation = staticmethod(lambda location: str(profile))
    from util.common.config import config
    from util.common.enum import DownloadType, MediaType
    from util.download.downloader.parse_worker import ParseWorker
    from util.download.parse.audio_info import AudioInfoParser
    from util.download.parse.video_info import VideoInfoParser
    from util.download.task.info import TaskInfo
    from util.format.file_name import FileNameFormatter
    from util.network.request import SyncNetWorkRequest, get_client
    from util.parse.additional.danmaku import DanmakuParser
    from util.parse.additional.player import PlayerInfoParser
    from util.parse.additional.subtitles import SubtitlesParser
    from util.parse.parser.video import VideoParser
    from util.parse.worker import WorkerBase

    assert all(callable(item) for item in (
        ParseWorker, AudioInfoParser, VideoInfoParser, FileNameFormatter,
        SyncNetWorkRequest, get_client, DanmakuParser, PlayerInfoParser, SubtitlesParser, VideoParser, WorkerBase
    ))
    assert MediaType.DASH and DownloadType.VIDEO
    task = TaskInfo()
    assert task.Basic and task.Episode and task.Download and task.File
    # A newer schema can rewrite the shared Bili23 profile at import time.
    assert config.app_config_version == 2130
    key = config.get(config.img_key)
    config.set(config.img_key, key, save=False)
    assert config.is_login.value is False
    assert not (profile / "Bili23 Downloader" / "config.json").exists()
    print("VEYLO_BILI_COMPATIBLE")


if __name__ == "__main__":
    main()
