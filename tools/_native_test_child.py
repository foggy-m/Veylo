"""Child entry for check_native.py. Executes one allowlisted test, never itself."""
import ctypes
from contextlib import redirect_stdout, redirect_stderr
import os
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
root, test, log = map(Path, sys.argv[1:4])
if root.resolve() != ROOT:
    raise SystemExit("Invalid test repository")
allowed = {ROOT/"tests/native_veylo_options_test.py", ROOT/"bilibili/tests/test_native_veylo_media.py"}
if test.resolve() not in allowed:
    raise SystemExit("Unknown native test")
os.chdir(ROOT)
sys.dont_write_bytecode = True
ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
sys.path.insert(0, str(test.parent))
with log.open("w", encoding="utf-8", buffering=1) as output, redirect_stdout(output), redirect_stderr(output):
    sys.argv = [str(test)]
    try:
        runpy.run_path(str(test), run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1) from None
