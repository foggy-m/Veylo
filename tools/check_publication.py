"""Inspect the actual Git index without printing secret values."""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {"tabbit_ud", "jobs", "archive-legacy", "fy-extension", "__pycache__", "startup-probe", "bounded-tests", ".build", "_backups"}
BLOCKED_SUFFIXES = {".exe", ".dll", ".log", ".pem", ".key", ".pfx", ".p12", ".zip", ".jsonl", ".pyc"}
PATTERNS = {
    "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "github_token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,})",
    "openai_key": rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{30,}",
    "aws_key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "personal_profile_path": rb"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+",
    "private_workspace_path": rb"[A-Za-z]:[\\/]+\.(?:codex|zcode)[\\/]",
    "netscape_cookie_row": rb"(?m)^(?:#HttpOnly_)?[^\t\r\n ]+\t(?:TRUE|FALSE)\t[^\t\r\n]*\t(?:TRUE|FALSE)\t[0-9]+\t[^\t\r\n]+\t[^\r\n]+$",
}

def main():
    raw = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=ROOT)
    entries = []
    issues = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        mode, sha, stage = metadata.split()
        path = encoded_path.decode("utf-8")
        item = PurePosixPath(path)
        if stage != b"0" or mode not in (b"100644", b"100755"):
            issues.append({"path": path, "category": "unsupported_index_mode"})
        if (set(item.parts) & BLOCKED_PARTS or any(part.startswith(("native_veylo_run_", "_archive")) for part in item.parts)
            or item.suffix.lower() in BLOCKED_SUFFIXES or item.name.startswith(".env")
            or item.name in {"Cookies", "LocalState", "Local State", "config.ini", "config.json"}
            or item.name.endswith("_paths.json") or item.name.startswith("com.shiguang.")
            or ("cookie" in item.name.lower() and item.suffix.lower() in {".txt", ".json"})):
            issues.append({"path": path, "category": "private_or_generated_file"})
        entries.append((path, sha))
    assert entries, "Stage the reviewed publication files first"
    result = subprocess.run(["git", "cat-file", "--batch"], input=b"".join(sha+b"\n" for _,sha in entries), cwd=ROOT, capture_output=True, check=True)
    cursor = 0
    total = 0
    for path, sha in entries:
        end = result.stdout.index(b"\n", cursor)
        actual_sha, kind, encoded_size = result.stdout[cursor:end].split()
        assert actual_sha == sha and kind == b"blob"
        size = int(encoded_size)
        data = result.stdout[end+1:end+1+size]
        assert len(data) == size
        cursor = end+size+2
        total += size
        for category, pattern in PATTERNS.items():
            for match in re.finditer(pattern, data):
                issues.append({"path": path, "line": data.count(b"\n", 0, match.start())+1, "category": category})
    manifest_data = subprocess.check_output(["git", "show", ":extension/manifest.json"], cwd=ROOT)
    manifest = json.loads(manifest_data)
    for key in ("content_scripts", "host_permissions", "web_accessible_resources"):
        if manifest.get(key):
            issues.append({"path": "extension/manifest.json", "category": "webpage_privileged_ui_exposure", "key": key})
    if manifest.get("externally_connectable"):
        issues.append({"path": "extension/manifest.json", "category": "external_messaging"})
    print(json.dumps({"staged_files": len(entries), "staged_bytes": total, "issues": issues}, ensure_ascii=True))
    return 1 if issues else 0

if __name__ == "__main__":
    sys.exit(main())
