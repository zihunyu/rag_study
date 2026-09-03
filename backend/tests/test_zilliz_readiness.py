from __future__ import annotations

from pathlib import Path

import pytest
from ragkb.adapters.zilliz_provision import (
    ZillizSyntheticLifecycleError,
    request_collection_load_if_needed,
    run_synthetic_lifecycle,
)
from ragkb.config import load_env
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan


class _ReadinessClient:
    def __init__(
        self,
        states: list[str],
        indexes: list[list[str]],
        *,
        fail_insert_at: int | None = None,
    ) -> None:
        self.states = states
        self.indexes = indexes
        self.state_calls = 0
        self.index_calls = 0
        self.insert_calls = 0
        self.fail_insert_at = fail_insert_at
        self.batch_sizes: list[int] = []
        self.stored: set[str] = set()
        self.deleted_ids: list[str] = []
        self.events: list[str] = []

    def get_load_state(self, **kwargs):
        self.events.append("load_state")
        value = self.states[min(self.state_calls, len(self.states) - 1)]
        self.state_calls += 1
        return {"state": value}

    def list_indexes(self, **kwargs):
        self.events.append("list_indexes")
        value = self.indexes[min(self.index_calls, len(self.indexes) - 1)]
        self.index_calls += 1
        return value

    def insert(self, **kwargs):
        self.events.append("insert")
        self.insert_calls += 1
        data = kwargs["data"]
        self.batch_sizes.append(len(data))
        if self.fail_insert_at == self.insert_calls:
            raise AttributeError("simulated MutationResult batch compatibility failure")
        self.stored.add(str(data[0]["zilliz_pk"]))
        return {"insert_count": 1}

    def get(self, **kwargs):
        self.events.append("get")
        return [{"zilliz_pk": item} for item in kwargs["ids"] if item in self.stored]

    def delete(self, **kwargs):
        self.events.append("delete")
        self.deleted_ids.extend(map(str, kwargs["ids"]))
        self.stored.difference_update(map(str, kwargs["ids"]))
        return {"delete_count": len(kwargs["ids"])}

    def load(self, **kwargs):
        self.events.append("load")
        raise AttributeError("simulated load return compatibility error")


def _settings():
    loaded = load_env(Path(__file__).resolve().parents[2])
    assert loaded.settings is not None
    return loaded.settings


def test_four_records_insert_one_by_one_only_after_ready_and_cleanup_all() -> None:
    settings = _settings()
    expected = [
        str(index["index_name"])
        for index in build_zilliz_collection_plan(settings)["schema"]["indexes"]
    ]
    client = _ReadinessClient(["Loading", "Loaded"], [[], expected])

    records = [{"zilliz_pk": f"synthetic-{index}"} for index in range(4)]
    result = run_synthetic_lifecycle(
        client,
        settings,
        records,
        lambda ids: {"confirmed": len(ids) == 4},
        total_timeout_seconds=5,
        max_polls=3,
        poll_interval_seconds=0,
        sleep=lambda _: None,
        monotonic=lambda: 0,
    )

    assert result["inserted_count"] == 4
    assert result["cleaned_count"] == 4
    assert result["remaining_count"] == 0
    assert result["readiness"]["poll_count"] == 2
    assert client.insert_calls == 4
    assert client.batch_sizes == [1, 1, 1, 1]
    assert client.deleted_ids == [f"synthetic-{index}" for index in range(4)]
    assert client.stored == set()
    assert client.events[-1] == "get"


def test_readiness_timeout_returns_explicit_error_and_never_inserts() -> None:
    settings = _settings()
    client = _ReadinessClient(["Loading"], [[]])

    with pytest.raises(ZillizSyntheticLifecycleError) as failed:
        run_synthetic_lifecycle(
            client,
            settings,
            [{"zilliz_pk": "must-not-write"}],
            lambda ids: {"confirmed": bool(ids)},
            total_timeout_seconds=1,
            max_polls=2,
            poll_interval_seconds=0,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )

    assert failed.value.stage == "wait_collection_ready"
    assert failed.value.error_type == "ZillizCollectionNotReady"
    assert failed.value.error_code == "ZILLIZ_COLLECTION_NOT_READY"
    assert failed.value.confirmed_count == 0
    assert client.insert_calls == 0
    assert "insert" not in client.events


def test_second_record_failure_stops_and_cleans_only_first_confirmed_id() -> None:
    settings = _settings()
    expected = [
        str(index["index_name"])
        for index in build_zilliz_collection_plan(settings)["schema"]["indexes"]
    ]
    client = _ReadinessClient(["Loaded"], [expected], fail_insert_at=2)

    with pytest.raises(ZillizSyntheticLifecycleError) as failed:
        run_synthetic_lifecycle(
            client,
            settings,
            [{"zilliz_pk": "first"}, {"zilliz_pk": "second"}],
            lambda ids: {"must_not_run": True},
            total_timeout_seconds=5,
            max_polls=2,
            poll_interval_seconds=0,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )

    assert failed.value.stage == "insert_synthetic_2"
    assert failed.value.error_type == "AttributeError"
    assert failed.value.confirmed_count == 1
    assert failed.value.cleaned_count == 1
    assert failed.value.remaining_count == 0
    assert client.insert_calls == 2
    assert client.batch_sizes == [1, 1]
    assert client.deleted_ids == ["first"]
    assert client.stored == set()


def test_already_loaded_collection_never_calls_load() -> None:
    settings = _settings()
    client = _ReadinessClient(["Loaded"], [[]])

    action = request_collection_load_if_needed(client, settings)

    assert action == "already_loaded"
    assert "load" not in client.events


def test_load_attribute_error_is_deferred_to_bounded_readiness_confirmation() -> None:
    settings = _settings()
    client = _ReadinessClient(["Loading", "Loaded"], [[]])

    action = request_collection_load_if_needed(client, settings)

    assert action == "load_return_attribute_error_requires_readiness_confirmation"
    assert client.events == ["load_state", "load"]
