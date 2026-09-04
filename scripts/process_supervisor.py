"""Ownership-safe native process state and lifecycle orchestration."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ProcessIdentity:
    name: str
    pid: int
    create_time: float
    executable: str
    command: str
    cwd: str
    owner_token: str


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    command: tuple[str, ...]
    cwd: Path


class ProcessInspector(Protocol):
    def inspect(self, pid: int, name: str) -> ProcessIdentity | None: ...

    def terminate(self, pid: int) -> None: ...

    def wait(self, pid: int, timeout_seconds: float) -> bool: ...

    def kill(self, pid: int) -> None: ...


class ProcessLauncher(Protocol):
    def start(self, spec: ProcessSpec, owner_token: str) -> ProcessIdentity: ...


def normalize_command(value: str) -> str:
    return " ".join(value.strip().split())


class LocalProcessSupervisor:
    def __init__(
        self,
        state_path: Path,
        inspector: ProcessInspector,
        launcher: ProcessLauncher,
    ) -> None:
        self.state_path = state_path
        self.inspector = inspector
        self.launcher = launcher

    @staticmethod
    def _matches(expected: ProcessIdentity, actual: ProcessIdentity | None) -> bool:
        return bool(
            actual
            and expected.pid == actual.pid
            and abs(expected.create_time - actual.create_time) <= 1.0
            and Path(expected.executable).resolve() == Path(actual.executable).resolve()
            and normalize_command(expected.command) == normalize_command(actual.command)
            and Path(expected.cwd).resolve() == Path(actual.cwd).resolve()
            and expected.owner_token
            and expected.owner_token == actual.owner_token
        )

    def _read(self) -> list[ProcessIdentity]:
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return [ProcessIdentity(**item) for item in payload["processes"]]

    def _write_atomic(self, identities: list[ProcessIdentity]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".local-stack-", suffix=".json", dir=self.state_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"processes": [asdict(item) for item in identities]}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def status(self) -> dict[str, object]:
        if not self.state_path.is_file():
            return {"running": False, "owned": [], "refused": []}
        owned: list[str] = []
        refused: list[str] = []
        for expected in self._read():
            actual = self.inspector.inspect(expected.pid, expected.name)
            (owned if self._matches(expected, actual) else refused).append(expected.name)
        return {"running": bool(owned), "owned": owned, "refused": refused}

    def start(self, specs: list[ProcessSpec]) -> list[ProcessIdentity]:
        if self.state_path.is_file():
            status = self.status()
            raise RuntimeError(
                "LOCAL_STACK_ALREADY_RUNNING"
                if status["owned"]
                else "LOCAL_STACK_STATE_OWNERSHIP_UNVERIFIED"
            )
        owner_token = secrets.token_urlsafe(24)
        started: list[ProcessIdentity] = []
        try:
            for spec in specs:
                started.append(self.launcher.start(spec, owner_token))
            self._write_atomic(started)
            return started
        except Exception:
            for identity in reversed(started):
                actual = self.inspector.inspect(identity.pid, identity.name)
                if self._matches(identity, actual):
                    self.inspector.terminate(identity.pid)
            raise

    def stop(self, timeout_seconds: float = 5.0) -> dict[str, list[str]]:
        if not self.state_path.is_file():
            return {"stopped": [], "refused": []}
        stopped: list[str] = []
        refused: list[str] = []
        for expected in self._read():
            actual = self.inspector.inspect(expected.pid, expected.name)
            if not self._matches(expected, actual):
                refused.append(expected.name)
                continue
            self.inspector.terminate(expected.pid)
            if not self.inspector.wait(expected.pid, timeout_seconds):
                actual = self.inspector.inspect(expected.pid, expected.name)
                if not self._matches(expected, actual):
                    refused.append(expected.name)
                    continue
                self.inspector.kill(expected.pid)
                self.inspector.wait(expected.pid, timeout_seconds)
            stopped.append(expected.name)
        if not refused:
            self.state_path.unlink(missing_ok=True)
        return {"stopped": stopped, "refused": refused}


class SubprocessLauncher:
    def __init__(self, inspector: ProcessInspector, wrapper: Path) -> None:
        self.inspector = inspector
        self.wrapper = wrapper

    def start(self, spec: ProcessSpec, owner_token: str) -> ProcessIdentity:
        command = [
            os.fspath(Path(sys.executable).resolve()),
            os.fspath(self.wrapper),
            "--owner-token",
            owner_token,
            "--owned-cwd",
            os.fspath(spec.cwd.resolve()),
            "--",
            *spec.command,
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=spec.cwd, creationflags=flags)  # noqa: S603
        deadline = time.time() + 3
        while time.time() < deadline:
            identity = self.inspector.inspect(process.pid, spec.name)
            if identity is not None:
                return identity
            time.sleep(0.05)
        process.terminate()
        raise RuntimeError(f"PROCESS_IDENTITY_UNAVAILABLE:{spec.name}")


class SystemProcessInspector:
    @staticmethod
    def _metadata(command: str) -> tuple[str, str]:
        owner = re.search(r"--owner-token\s+(\S+)", command)
        cwd = re.search(r'--owned-cwd\s+(?:"([^"]+)"|(\S+))', command)
        return (
            owner.group(1) if owner else "",
            (cwd.group(1) or cwd.group(2)) if cwd else "",
        )

    def inspect(self, pid: int, name: str) -> ProcessIdentity | None:
        if os.name == "nt":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if powershell is None:
                return None
            script = (
                f'$c=Get-CimInstance Win32_Process -Filter "ProcessId={pid}";'
                f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue;"
                "if($c -and $p){[pscustomobject]@{Executable=$c.ExecutablePath;"
                "Command=$c.CommandLine;CreateTime=([DateTimeOffset]$p.StartTime)."
                "ToUnixTimeMilliseconds()/1000}|ConvertTo-Json -Compress}"
            )
            completed = subprocess.run(  # noqa: S603
                [powershell, "-NoProfile", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode or not completed.stdout.strip():
                return None
            payload = json.loads(completed.stdout)
            command = str(payload.get("Command") or "")
            owner, cwd = self._metadata(command)
            return ProcessIdentity(
                name,
                pid,
                float(payload["CreateTime"]),
                str(payload.get("Executable") or ""),
                normalize_command(command),
                cwd,
                owner,
            )
        proc = Path("/proc") / str(pid)
        try:
            command = proc.joinpath("cmdline").read_bytes().replace(b"\x00", b" ").decode()
            executable = os.readlink(proc / "exe")
            cwd = os.readlink(proc / "cwd")
            create_time = proc.stat().st_ctime
        except (FileNotFoundError, PermissionError, OSError):
            return None
        owner, owned_cwd = self._metadata(command)
        return ProcessIdentity(
            name,
            pid,
            create_time,
            executable,
            normalize_command(command),
            owned_cwd or cwd,
            owner,
        )

    def terminate(self, pid: int) -> None:
        if os.name == "nt":
            taskkill = shutil.which("taskkill") or str(
                Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
            )
            subprocess.run(  # noqa: S603
                [taskkill, "/PID", str(pid), "/T"],
                check=False,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return
        os.kill(pid, signal.SIGTERM)

    def wait(self, pid: int, timeout_seconds: float) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.inspect(pid, "process") is None:
                return True
            time.sleep(0.05)
        return False

    def kill(self, pid: int) -> None:
        if os.name == "nt":
            taskkill = shutil.which("taskkill") or str(
                Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
            )
            subprocess.run(  # noqa: S603
                [taskkill, "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return
        os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
