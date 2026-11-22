"""Exercise the updater after installing Setup on the clean Actions VM."""
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time

from setup_clean_install_test import APP_DIR, ROOT, ORIGIN, SETUP, assert_registrations, command


def bridge_call(bridge, request):
    body = json.dumps(request).encode("utf-8")
    process = subprocess.run([str(bridge), ORIGIN], input=struct.pack("<I", len(body)) + body,
                             capture_output=True, check=True, timeout=15)
    assert len(process.stdout) >= 4 and struct.unpack("<I", process.stdout[:4])[0] == len(process.stdout) - 4
    result = json.loads(process.stdout[4:])
    assert result["ok"], result.get("error")
    return result


def smoke_updated_x(bridge, directory):
    task = bridge_call(bridge, {"action": "x-download", "url": "https://x.com/NASA/status/1574539270987173903",
                                "quality": "480", "dir": str(directory)})
    assert task["cookieStatus"] == "guest"
    job_id = task["jobId"]
    deadline = time.monotonic() + 75
    try:
        while time.monotonic() < deadline:
            job = bridge_call(bridge, {"action": "job-status", "jobId": job_id})["job"]
            if job["state"] == "completed":
                assert job["files"] and all(Path(file).parent == directory and Path(file).stat().st_size > 0 for file in job["files"])
                return "passed"
            if job["state"] == "failed":
                return "upstream-request-failed"
            time.sleep(.6)
        return "upstream-request-timed-out"
    finally:
        bridge_call(bridge, {"action": "cancel-job", "jobId": job_id})


def main():
    assert os.environ.get("GITHUB_ACTIONS") == "true", "Use a clean Actions Windows profile"
    root = Path(os.environ["VEYLO_TEST_INSTALL_DIR"])
    app = root / APP_DIR
    setup = SETUP
    manager = app / "Veylo-Setup.exe"
    assert manager.read_bytes() == setup.read_bytes(), "Start-menu manager uses the verified installer"
    programs = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    assert (programs / "Veylo 安装与更新.lnk").is_file(), "Installed setup manager shortcut is missing"
    bridge = app / "native/ShiguangBridge.exe"
    selection = bridge.parent / "yt-dlp-version.txt"
    bili_paths = app / "bilibili/native/bili_paths.json"
    stable_bili = bili_paths.read_bytes()
    assert bridge_call(bridge, {"action": "ping"})["engineSource"] == "bundled"

    bili = Path(json.loads(stable_bili)["bili_root"])
    with tempfile.TemporaryDirectory(prefix="veylo-bili-candidate-profile-") as temporary:
        assert "VEYLO_BILI_COMPATIBLE" in command(
            bili / "runtime/python.exe", "-B", app / "bili-probe.py", app / "bilibili/driver/bd_driver.py",
            bili, Path(temporary).resolve()), "The Bili probe must accept the verified, headless runtime"
    assert not (Path(os.environ["APPDATA"]) / "Bili23 Downloader/config.json").exists()
    report_path = ROOT / "dist/clean-install-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    update = command(manager, "--update-components", timeout=240)
    print(update, flush=True)
    assert re.search(r"Bili23 2\.14\.0 · 官方 \d+\.\d+\.\d+ 等待 Veylo 兼容验证", update)
    assert bili_paths.read_bytes() == stable_bili, "Upstream Bili23 must wait for a Veylo-compatible release"
    chosen = selection.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", chosen)
    assert command(root / "vendor" / f"yt-dlp-{chosen}" / "yt-dlp.exe", "--version").strip() == chosen
    assert bridge_call(bridge, {"action": "ping"})["engineSource"] == "managed"
    live = smoke_updated_x(bridge, root.resolve() / "update-download-test")
    if any(item["platform"] == "x" and item["liveDownload"] == "completed" for item in report["liveGuestTasks"]):
        assert live == "passed", "The same X sample failed after switching engines"
    print("PASS verified latest component switching, X download check and incompatible Bili hold", flush=True)

    selection.write_text("../malformed", encoding="utf-8")
    assert bridge_call(bridge, {"action": "ping"})["engineSource"] == "bundled", "Malformed selection must fall back"
    selection.write_text(chosen, encoding="utf-8")
    command(setup, "--quiet", timeout=80)
    assert_registrations(root)
    assert selection.read_text(encoding="utf-8").strip() == chosen
    assert bridge_call(bridge, {"action": "ping"})["engineSource"] == "managed", "Reinstall must preserve a valid selection"
    follow = app / "check-updates-on-open.txt"
    follow.write_text("yes", encoding="utf-8")
    command(manager, "--restore-engine")
    assert follow.read_text(encoding="utf-8") == "no"
    assert bridge_call(bridge, {"action": "ping"})["engineSource"] == "bundled"
    assert bili_paths.read_bytes() == stable_bili
    print("PASS invalid selection fallback, rerun retention and one-click restore", flush=True)

    report["componentUpdates"] = {
        "managedInstallerShortcut": "passed",
        "biliPreviewProbe": "passed",
        "unverifiedBiliUpgrade": "held",
        "managedYtDlp": chosen,
        "updatedXDownload": live,
        "reinstallPreservedSelection": "passed",
        "restoreBundledEngine": "passed",
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
