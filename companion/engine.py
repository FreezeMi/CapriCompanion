"""Starts, checks and stops the local llama.cpp server that runs the language model.

Security choices:
- The server listens on 127.0.0.1 only, on a random free port.
- Every request needs a random key that is created fresh at each start and passed
  through an environment variable, so it never appears on a command line.
- The server is placed in a Windows job object that ends it when this app exits,
  even after a crash, so it can't keep running and holding memory in the background.
- It runs at below-normal priority, so the rest of the PC stays responsive.
"""

from __future__ import annotations

import ctypes
import http.client
import json
import os
import secrets
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .config import DATA, Settings, resolve


class EngineError(RuntimeError):
    pass


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _KillOnCloseJob:
    """A Windows job object whose processes end when this app's handle to it closes."""

    _EXTENDED_LIMIT_INFORMATION = 9
    _KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            self.handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def add(self, pid: int) -> None:
        process = self._kernel32.OpenProcess(
            self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE, False, pid
        )
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# Only what the engine needs to run; nothing else from this app's environment.
_INHERITED = (
    "SystemRoot",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


class Engine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.device = settings.device
        self.process: subprocess.Popen | None = None
        self.port = 0
        self.key = ""
        self._job: _KillOnCloseJob | None = None
        self._log = None

    def arguments(self, executable: Path, model: Path) -> list[str]:
        s = self.settings
        args = [
            str(executable),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--model", str(model),
            "--alias", "companion",
            "--ctx-size", str(s.context_tokens),
            "--threads", str(s.threads),
            "--threads-batch", str(s.threads_batch),
            "--parallel", "1",
            "--no-webui",
            "--jinja",
            "--reasoning", "off",
            "--flash-attn", "on",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
        ]  # fmt: skip
        if self.device == "gpu":
            args += ["--device", "Vulkan0", "--n-gpu-layers", "99"]
        else:
            args += ["--device", "none", "--n-gpu-layers", "0"]
        return args

    def start(self) -> None:
        executable = resolve(self.settings.engine)
        model = resolve(self.settings.model)
        if not executable.is_file():
            raise EngineError("The AI engine isn't installed yet. Run Install.cmd first.")
        if not model.is_file():
            raise EngineError("The language model isn't downloaded yet. Run Install.cmd first.")
        self.stop()
        self.port = _free_port()
        self.key = secrets.token_urlsafe(32)
        env = {name: os.environ[name] for name in _INHERITED if name in os.environ}
        env["LLAMA_API_KEY"] = self.key
        DATA.mkdir(exist_ok=True)
        self._log = open(DATA / "engine.log", "wb")
        self.process = subprocess.Popen(
            self.arguments(executable, model),
            cwd=str(executable.parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
        )
        try:
            if self._job is None:
                self._job = _KillOnCloseJob()
            self._job.add(self.process.pid)
        except OSError:
            pass  # Still stopped normally on exit; only crash cleanup is lost.

    def wait_until_ready(
        self, timeout: float = 300, cancelled: Callable[[], bool] = lambda: False
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                raise EngineError("Stopped while loading.")
            if self.process is None or self.process.poll() is not None:
                code = None if self.process is None else self.process.returncode
                raise EngineError(
                    f"The AI engine stopped while loading (exit code {code}). "
                    "Details are in data/engine.log."
                )
            try:
                status, _ = self.request("GET", "/health", timeout=2)
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.25)
        raise EngineError("The AI engine took too long to load. Details are in data/engine.log.")

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def connection(self, timeout: float) -> http.client.HTTPConnection:
        if not self.port:
            raise EngineError("The AI engine isn't running.")
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def request(
        self, method: str, path: str, body: dict | None = None, timeout: float = 30
    ) -> tuple[int, dict]:
        conn = self.connection(timeout)
        try:
            payload = None if body is None else json.dumps(body).encode("utf-8")
            conn.request(method, path, body=payload, headers=self.headers())
            response = conn.getresponse()
            data = response.read()
            try:
                parsed = json.loads(data) if data else {}
            except ValueError:
                parsed = {}
            return response.status, parsed
        finally:
            conn.close()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        self.port = 0
        if self._log is not None:
            self._log.close()
            self._log = None
