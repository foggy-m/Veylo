"""Launch one test in a non-breakaway Windows Job Object, then destroy the job."""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import sys
import time

if sys.platform != "win32":
    raise SystemExit("Windows native tests require Windows.")

K = c.WinDLL("kernel32", use_last_error=True)
S = c.c_size_t
class Basic(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong), ("flags", w.DWORD),
                ("minimum_ws", S), ("maximum_ws", S), ("active_limit", w.DWORD),
                ("affinity", S), ("priority", w.DWORD), ("scheduling", w.DWORD)]
class IO(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
class Extended(c.Structure):
    _fields_ = [("basic", Basic), ("io", IO), ("process_memory", S), ("job_memory", S), ("peak_process", S), ("peak_job", S)]
class Startup(c.Structure):
    _fields_ = [("cb",w.DWORD), ("reserved",w.LPWSTR), ("desktop",w.LPWSTR), ("title",w.LPWSTR)] + [(n,w.DWORD) for n in ("x","y","xs","ys","xc","yc","fill","flags")] + [("show",w.WORD),("reserved_size",w.WORD),("reserved_data",c.c_void_p),("stdin",w.HANDLE),("stdout",w.HANDLE),("stderr",w.HANDLE)]
class Process(c.Structure):
    _fields_ = [("process",w.HANDLE),("thread",w.HANDLE),("process_id",w.DWORD),("thread_id",w.DWORD)]
class Accounting(c.Structure):
    _fields_ = [(n,c.c_longlong) for n in ("user","kernel","period_user","period_kernel")] + [(n,w.DWORD) for n in ("faults","total","active","terminated")]
K.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR]; K.CreateJobObjectW.restype=w.HANDLE
K.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]; K.SetInformationJobObject.restype=w.BOOL
K.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]; K.QueryInformationJobObject.restype=w.BOOL
K.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]; K.AssignProcessToJobObject.restype=w.BOOL
K.CreateProcessW.argtypes=[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(Startup),c.POINTER(Process)]; K.CreateProcessW.restype=w.BOOL
K.ResumeThread.argtypes=[w.HANDLE]; K.ResumeThread.restype=w.DWORD
K.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]; K.WaitForSingleObject.restype=w.DWORD
K.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]; K.GetExitCodeProcess.restype=w.BOOL
K.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]; K.TerminateJobObject.restype=w.BOOL
K.TerminateProcess.argtypes=[w.HANDLE,w.UINT]; K.TerminateProcess.restype=w.BOOL
K.CloseHandle.argtypes=[w.HANDLE]; K.CloseHandle.restype=w.BOOL

def require(ok, label):
    if not ok: raise OSError(c.get_last_error(), label)

def run_test(test, seconds, work):
    root=Path(__file__).resolve().parents[1]
    test=test.resolve()
    assert 1 <= seconds <= 600
    assert test.is_file() and test.suffix == ".py"
    work.mkdir(parents=True, exist_ok=True)
    log=work/(test.stem+".log")
    args=[sys.executable,"-B","-u",str(root/"tools/_native_test_child.py"),str(root),str(test),str(log)]
    job=K.CreateJobObjectW(None,None); require(job,"CreateJobObject")
    process=Process(); created=False; code=w.DWORD(1); peak_active=0
    try:
        limits=Extended()
        # Active-process + memory limits, no breakaway flags, kill all members on close.
        limits.basic.flags=0x0008|0x0100|0x0200|0x0400|0x2000
        limits.basic.active_limit=12
        limits.process_memory=256*1024*1024
        limits.job_memory=768*1024*1024
        require(K.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)),"Set limits")
        readback=Extended()
        require(K.QueryInformationJobObject(job,9,c.byref(readback),c.sizeof(readback),None),"Read back limits")
        assert readback.basic.active_limit == 12 and readback.job_memory == limits.job_memory
        startup=Startup(); startup.cb=c.sizeof(startup)
        # Start suspended so NO test code can execute before assignment to the bounded job.
        require(K.CreateProcessW(sys.executable,c.create_unicode_buffer(subprocess.list2cmdline(args)),None,None,False,0x0004|0x08000000,None,str(root),c.byref(startup),c.byref(process)),"Create suspended test")
        created=True
        require(K.AssignProcessToJobObject(job,process.process),"Assign suspended test to bounded job")
        require(K.ResumeThread(process.thread) != 0xFFFFFFFF,"Resume bounded test")
        K.CloseHandle(process.thread);process.thread=None
        started=time.monotonic(); last=started
        print(json.dumps({"test":test.name,"hard_process_limit":12,"hard_job_memory_mib":768,"wall_timeout_seconds":seconds}),flush=True)
        while True:
            status=K.WaitForSingleObject(process.process,1000)
            accounting=Accounting()
            require(K.QueryInformationJobObject(job,1,c.byref(accounting),c.sizeof(accounting),None),"Query bounded job")
            peak_active=max(peak_active,accounting.active)
            if status==0:break
            if status!=258:raise OSError("Unexpected wait status")
            elapsed=time.monotonic()-started
            if elapsed>seconds:
                require(K.TerminateJobObject(job,124),"Terminate timed-out test job")
                K.WaitForSingleObject(process.process,5000)
                break
            if time.monotonic()-last>=30:
                print(json.dumps({"running":test.name,"elapsed_seconds":round(elapsed),"active_processes":accounting.active}),flush=True);last=time.monotonic()
        require(K.GetExitCodeProcess(process.process,c.byref(code)),"Read test exit")
        require(K.QueryInformationJobObject(job,9,c.byref(readback),c.sizeof(readback),None),"Read peak memory")
        print(json.dumps({"test":test.name,"exit":code.value,"peak_active_sampled":peak_active,"peak_job_memory_mib":round(readback.peak_job/1024/1024,1),"log":str(log)}),flush=True)
    finally:
        if created:
            K.TerminateJobObject(job,125)
            K.TerminateProcess(process.process,125)
            if process.thread:K.CloseHandle(process.thread)
            K.CloseHandle(process.process)
        K.CloseHandle(job)
    if log.exists():print(log.read_text(encoding="utf-8")[-1800:],flush=True)
    return 0 if code.value==0 else 1

def main():
    import argparse
    if sys.platform != "win32":
        raise SystemExit("Windows native tests require Windows.")
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=["all", "youtube", "bili"], nargs="?", default="all")
    parser.add_argument("--log-dir", type=Path, default=root/".test-results/native")
    args = parser.parse_args()
    tests = []
    if args.suite in ("all", "youtube"):
        tests.append(root/"tests/native_veylo_options_test.py")
    if args.suite in ("all", "bili"):
        tests.append(root/"bilibili/tests/test_native_veylo_media.py")
    for test in tests:
        if run_test(test, 480, args.log_dir.resolve()):
            return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
