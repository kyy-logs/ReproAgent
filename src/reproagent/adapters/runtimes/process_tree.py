from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes as w

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                    ("x", w.DWORD), ("y", w.DWORD), ("xsize", w.DWORD), ("ysize", w.DWORD),
                    ("xchars", w.DWORD), ("ychars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                    ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_bytes", ctypes.c_void_p),
                    ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", w.DWORD),
                    ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t), ("active_limit", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("reads", "writes", "others", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [("user", ctypes.c_longlong), ("kernel", ctypes.c_longlong), ("period_user", ctypes.c_longlong),
                    ("period_kernel", ctypes.c_longlong), ("faults", w.DWORD), ("total", w.DWORD),
                    ("active", w.DWORD), ("terminated", w.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                            ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result

    def checked(success):
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())


class ManagedProcess:
    @classmethod
    def spawn(cls, argv: tuple[str, ...], cwd: Path, env: dict[str, str]):
        if not argv or not Path(argv[0]).is_absolute():
            raise ValueError("execution requires an absolute executable path")
        self = cls()
        self._closed = False
        if os.name != "nt":
            self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            start_new_session=True, bufsize=0)
            self.pid = self.process.pid
            self.stdout, self.stderr = self.process.stdout, self.process.stderr
            return self
        self.job = kernel.CreateJobObjectW(None, None)
        checked(self.job)
        self.handle = None
        descriptors = []
        info = ProcessInfo()
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            out_read, out_write = os.pipe()
            descriptors.extend([out_read, out_write])
            err_read, err_write = os.pipe()
            descriptors.extend([err_read, err_write])
            stdin = os.open(os.devnull, os.O_RDONLY)
            descriptors.append(stdin)
            for descriptor in (out_write, err_write, stdin):
                os.set_inheritable(descriptor, True)
            startup = StartupInfo()
            startup.cb = ctypes.sizeof(startup)
            startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.stdin = msvcrt.get_osfhandle(stdin)
            startup.stdout = msvcrt.get_osfhandle(out_write)
            startup.stderr = msvcrt.get_osfhandle(err_write)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
            checked(kernel.CreateProcessW(argv[0], command, None, None, True,
                                          0x4 | 0x400 | 0x08000000, environment, str(cwd), ctypes.byref(startup), ctypes.byref(info)))
            self.handle, self.pid = info.process, info.pid
            checked(kernel.AssignProcessToJobObject(self.job, self.handle))
            if kernel.ResumeThread(info.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(info.thread)
            info.thread = None
            for descriptor in (out_write, err_write, stdin):
                os.close(descriptor)
                descriptors.remove(descriptor)
            self.stdout = os.fdopen(out_read, "rb", buffering=0)
            descriptors.remove(out_read)
            self.stderr = os.fdopen(err_read, "rb", buffering=0)
            descriptors.remove(err_read)
            return self
        except BaseException:
            if self.handle:
                kernel.TerminateProcess(self.handle, 137)
                kernel.WaitForSingleObject(self.handle, 1000)
                kernel.CloseHandle(self.handle)
            if info.thread:
                kernel.CloseHandle(info.thread)
            for descriptor in descriptors:
                os.close(descriptor)
            kernel.CloseHandle(self.job)
            raise

    def poll(self):
        if os.name != "nt":
            return self.process.poll()
        if kernel.WaitForSingleObject(self.handle, 0) == 258:
            return None
        code = w.DWORD()
        checked(kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)))
        return code.value

    def _group_live(self):
        if Path("/proc").is_dir():
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    values = path.read_text().rpartition(")")[2].split()
                    if len(values) > 2 and values[2] == str(self.pid) and values[0] != "Z":
                        return True
                except (OSError, ValueError):
                    pass
            return False
        try:
            os.killpg(self.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def terminate_tree(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        if os.name == "nt":
            if not kernel.TerminateJobObject(self.job, 137):
                return False
            while time.monotonic() < end:
                accounting = Accounting()
                if not kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    return False
                if accounting.active == 0:
                    return True
                time.sleep(0.02)
            return False
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=max(0.001, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
        while time.monotonic() < end:
            if not self._group_live():
                return True
            time.sleep(0.02)
        return False

    def close(self):
        if self._closed:
            return
        self._closed = True
        if os.name == "nt":
            kernel.CloseHandle(self.job)
            kernel.CloseHandle(self.handle)
        self.stdout.close()
        self.stderr.close()
