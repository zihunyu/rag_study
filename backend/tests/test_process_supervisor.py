from __future__ import annotations

from pathlib import Path

import pytest

from scripts.process_supervisor import (
    LocalProcessSupervisor,
    ProcessIdentity,
    ProcessSpec,
)


def _identity(name: str, pid: int, marker: str = "owner-marker") -> ProcessIdentity:
    return ProcessIdentity(
        name=name,
        pid=pid,
        create_time=100.0,
        executable="C:/Python/python.exe",
        command=f"python owned_process.py --owner-token {marker} --owned-cwd C:/workspace",
        cwd="C:/workspace",
        owner_token=marker,
    )


class _Inspector:
    def __init__(self, identities: dict[int, ProcessIdentity], *, exits: bool = True) -> None:
        self.identities = identities
        self.exits = exits
        self.terminated: list[int] = []
        self.killed: list[int] = []

    def inspect(self, pid: int, name: str) -> ProcessIdentity | None:
        del name
        return self.identities.get(pid)

    def terminate(self, pid: int) -> None:
        self.terminated.append(pid)
        if self.exits:
            self.identities.pop(pid, None)

    def wait(self, pid: int, timeout_seconds: float) -> bool:
        del timeout_seconds
        return pid not in self.identities

    def kill(self, pid: int) -> None:
        self.killed.append(pid)
        self.identities.pop(pid, None)


class _Launcher:
    def __init__(self, inspector: _Inspector, fail_on: str | None = None) -> None:
        self.inspector = inspector
        self.fail_on = fail_on
        self.next_pid = 100

    def start(self, spec: ProcessSpec, owner_token: str) -> ProcessIdentity:
        if spec.name == self.fail_on:
            raise RuntimeError("injected partial start failure")
        identity = _identity(spec.name, self.next_pid, owner_token)
        self.next_pid += 1
        self.inspector.identities[identity.pid] = identity
        return identity


def test_stop_refuses_forged_stale_and_pid_reused_state_without_signal(tmp_path: Path) -> None:
    expected = _identity("backend", 10)
    reused = ProcessIdentity(**{**expected.__dict__, "create_time": 999.0, "owner_token": "other"})
    inspector = _Inspector({10: reused})
    supervisor = LocalProcessSupervisor(tmp_path / "state.json", inspector, _Launcher(inspector))
    supervisor._write_atomic([expected])

    result = supervisor.stop()

    assert result == {"stopped": [], "refused": ["backend"]}
    assert inspector.terminated == inspector.killed == []
    assert (tmp_path / "state.json").is_file()


def test_owned_stop_and_duplicate_start_are_safe(tmp_path: Path) -> None:
    identity = _identity("backend", 10)
    inspector = _Inspector({10: identity})
    supervisor = LocalProcessSupervisor(tmp_path / "state.json", inspector, _Launcher(inspector))
    supervisor._write_atomic([identity])

    with pytest.raises(RuntimeError, match="ALREADY_RUNNING"):
        supervisor.start([ProcessSpec("backend", ("python",), tmp_path)])
    result = supervisor.stop()

    assert result == {"stopped": ["backend"], "refused": []}
    assert inspector.terminated == [10]
    assert not (tmp_path / "state.json").exists()


def test_partial_start_failure_cleans_only_verified_owned_children(tmp_path: Path) -> None:
    inspector = _Inspector({})
    supervisor = LocalProcessSupervisor(
        tmp_path / "state.json", inspector, _Launcher(inspector, fail_on="worker")
    )

    with pytest.raises(RuntimeError, match="partial"):
        supervisor.start(
            [
                ProcessSpec("backend", ("python",), tmp_path),
                ProcessSpec("worker", ("python",), tmp_path),
            ]
        )

    assert inspector.terminated == [100]
    assert not (tmp_path / "state.json").exists()


def test_escalation_revalidates_identity_before_kill(tmp_path: Path) -> None:
    identity = _identity("backend", 10)
    inspector = _Inspector({10: identity}, exits=False)
    supervisor = LocalProcessSupervisor(tmp_path / "state.json", inspector, _Launcher(inspector))
    supervisor._write_atomic([identity])

    def replace_on_wait(pid: int, timeout_seconds: float) -> bool:
        del timeout_seconds
        inspector.identities[pid] = ProcessIdentity(
            **{**identity.__dict__, "owner_token": "reused"}
        )
        return False

    inspector.wait = replace_on_wait  # type: ignore[method-assign]
    result = supervisor.stop()

    assert result["refused"] == ["backend"]
    assert inspector.killed == []
