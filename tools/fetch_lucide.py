"""Vendor a small reviewed, commit-pinned subset of Lucide SVGs without npm."""
import concurrent.futures
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "extension"
TARGET = ROOT / "third_party" / "lucide"
NAMES = [
    "download", "folder-open", "history", "music-2", "subtitles", "video", "copy",
    "arrow-up-right", "circle-check", "circle-alert", "x", "chevron-down", "settings-2",
    "loader-circle", "link", "refresh-cw", "external-link", "chevron-left", "info", "check",
    "bell", "keyboard", "ellipsis",
]
ALLOWED_TAGS = {"svg", "g", "path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}
ALLOWED_ATTRS = {
    "xmlns", "width", "height", "viewBox", "fill", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "d", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "points",
    "opacity", "fill-rule", "clip-rule",
}


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Veylo-Local-Asset-Collector/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        content = response.read(300_000)
    return content.decode("utf-8")


def validate_svg(text):
    root = ET.fromstring(text)
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ALLOWED_TAGS:
            raise ValueError("Unexpected SVG element: " + tag)
        for key, value in node.attrib.items():
            if key not in ALLOWED_ATTRS or "url(" in value.lower() or "javascript:" in value.lower():
                raise ValueError("Unexpected SVG attribute: " + key)
    if root.tag.rsplit("}", 1)[-1] != "svg" or root.get("viewBox") != "0 0 24 24":
        raise ValueError("Unexpected icon viewBox")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
    return re.sub(r"\s+", " ", text)


def main():
    commit = json.loads(fetch("https://api.github.com/repos/lucide-icons/lucide/commits/main"))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid upstream commit")
    base = "https://raw.githubusercontent.com/lucide-icons/lucide/" + commit + "/"
    license_text = fetch(base + "LICENSE")
    if "ISC" not in license_text or "Feather" not in license_text:
        raise ValueError("Unexpected license")
    aliases = {"history": "rotate-ccw", "subtitles": "captions"}
    def icon(name):
        source_name = aliases.get(name, name)
        return name, validate_svg(fetch(base + "icons/" + source_name + ".svg"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        icons = dict(pool.map(icon, NAMES))
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "LICENSE").write_text(license_text, encoding="utf-8")
    for name, text in icons.items():
        (TARGET / (name + ".svg")).write_text(text + "\n", encoding="utf-8")
    provenance = {"repository": "https://github.com/lucide-icons/lucide", "commit": commit,
                  "license": "ISC; retained MIT attribution for Feather-derived icons", "icons": NAMES,
                  "sourceAliases": aliases, "runtimeNetworkRequests": False}
    (TARGET / "SOURCE.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    output = "// Lucide icons: ISC + Feather MIT attribution in third_party/lucide/LICENSE.\n"
    output += "var SGIcons = (() => {\n  'use strict';\n  const icons = " + json.dumps(icons, ensure_ascii=False, indent=2) + ";\n"
    output += "  function svg(name, size = 18) {\n    const source = icons[name];\n    if (!source) return '';\n    const pixels = Number.isFinite(size) ? Math.max(12, Math.min(48, size)) : 18;\n"
    output += "    return source.replace(/width=\"24\"/, `width=\"${pixels}\"`).replace(/height=\"24\"/, `height=\"${pixels}\"`).replace('<svg ', '<svg aria-hidden=\"true\" focusable=\"false\" ');\n  }\n"
    output += "  return Object.freeze({ svg });\n})();\n"
    (ROOT / "icons.js").write_text(output, encoding="utf-8")
    print(json.dumps({"commit": commit, "icons": len(icons), "license": "ISC + retained Feather MIT", "networkAtRuntime": False}))


if __name__ == "__main__":
    main()
