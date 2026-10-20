"""Run dependency-free offline checks from the repository root."""
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in ROOT.rglob("*.py"):
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path.relative_to(ROOT)))
for path in [*ROOT.rglob("*.js"), *ROOT.rglob("*.cjs")]:
    subprocess.run(["node", "--check", str(path)], cwd=ROOT, check=True)
for test in ["tests/extension_shared_manifest_test.js", "tests/extension_background_test.js",
             "tests/extension_offscreen_popup_test.js", "tests/veylo_background_test.js",
             "extension/tests/ui.test.cjs"]:
    subprocess.run(["node", test], cwd=ROOT, check=True)
for test in ["tests/installer_test.py", "tests/publication_security_test.py"]:
    subprocess.run([sys.executable, "-B", test], cwd=ROOT, check=True)
print("PASS offline syntax, extension, installer and output-boundary checks")
