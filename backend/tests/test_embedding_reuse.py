from pathlib import Path

import pytest
from ragkb.adapters.model_http import OpenAICompatibleEmbeddingAdapter
from ragkb.config.env import EnvSettings
from ragkb.domain.errors import InvalidProviderResponse


class Transport:
    real_network = False

    def __init__(self):
        self.calls = []
        self.fail_on = 0

    def post_json(self, url, **kwargs):
        values = kwargs["payload"]["input"]
        self.calls.append(values)
        if self.fail_on == len(self.calls):
            raise RuntimeError("interrupted batch")
        return {
            "data": [
                {"index": i, "embedding": [float(len(value)), 1.0]}
                for i, value in reversed(list(enumerate(values)))
            ]
        }


def settings(**updates):
    return EnvSettings(embedding_model="test", embedding_dimension=2).model_copy(update=updates)


def test_provider_response_is_reordered_to_match_input():
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), transport=Transport())
    assert adapter.embed(["a", "long"]) == [[1.0, 1.0], [4.0, 1.0]]


@pytest.mark.parametrize("indexes", [[0, 0], [1, 2], [None, 1], [True, 0]])
def test_invalid_provider_indexes_fail_closed(indexes):
    class Bad(Transport):
        def post_json(self, url, **kwargs):
            return {"data": [{"index": i, "embedding": [1.0, 1.0]} for i in indexes]}

    with pytest.raises(InvalidProviderResponse):
        OpenAICompatibleEmbeddingAdapter(settings(), transport=Bad()).embed(["a", "b"])


def test_batching_limits_both_items_and_total_tokens():
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(
            embedding_batch_size=2,
            embedding_max_input_tokens=4,
            embedding_max_batch_tokens=5,
        ),
        transport=transport,
    )
    assert adapter.embed(["aaa", "bbb", "c", "dd"]) == [
        [3.0, 1.0],
        [3.0, 1.0],
        [1.0, 1.0],
        [2.0, 1.0],
    ]
    assert transport.calls == [["aaa"], ["bbb", "c"], ["dd"]]


def test_over_budget_input_is_rejected_before_any_provider_call():
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(embedding_max_input_tokens=4), transport=transport
    )
    with pytest.raises(ValueError, match="EMBEDDING_INPUT_TOKEN_LIMIT"):
        adapter.embed(["a", "toolong"])
    assert transport.calls == []


def test_completed_batches_survive_restart_and_only_missing_text_is_embedded(tmp_path: Path):
    from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache

    cache = SQLiteEmbeddingCache(tmp_path / "cache.sqlite3")
    transport = Transport()
    transport.fail_on = 2
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(embedding_batch_size=1), transport=transport, cache=cache
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        adapter.embed(["a", "bb"])
    resumed = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(), transport=resumed, cache=SQLiteEmbeddingCache(cache.path)
    )
    assert adapter.embed(["bb", "a", "bb"]) == [[2.0, 1.0], [1.0, 1.0], [2.0, 1.0]]
    assert resumed.calls == [["bb"]]
    assert adapter.embed(["a"]) == [[1.0, 1.0]]  # same cache is also used by query embeddings
    assert resumed.calls == [["bb"]]


def test_model_revision_and_exact_input_isolate_cached_vectors(tmp_path):
    from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache

    cache = SQLiteEmbeddingCache(tmp_path / "cache.sqlite3")
    transport = Transport()
    OpenAICompatibleEmbeddingAdapter(settings(), transport=transport, cache=cache).embed(["a"])
    OpenAICompatibleEmbeddingAdapter(
        settings(embedding_model="another"), transport=transport, cache=cache
    ).embed(["a"])
    OpenAICompatibleEmbeddingAdapter(settings(), transport=transport, cache=cache).embed([" a"])
    assert transport.calls == [["a"], ["a"], [" a"]]


def test_concurrent_callers_share_one_completed_provider_result(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache

    cache = SQLiteEmbeddingCache(tmp_path / "cache.sqlite3")
    transport = Transport()
    barrier = threading.Barrier(4)

    def call(_):
        adapter = OpenAICompatibleEmbeddingAdapter(settings(), transport=transport, cache=cache)
        barrier.wait(timeout=5)
        return adapter.embed(["same"])

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(call, range(4))) == [[[4.0, 1.0]]] * 4
    assert transport.calls == [["same"]]


def test_query_cache_can_be_disabled_without_disabling_document_cache(tmp_path):
    from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache

    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(query_embedding_cache_enabled=False),
        transport=transport,
        cache=SQLiteEmbeddingCache(tmp_path / "cache.sqlite3"),
    )
    adapter.embed(["same"])
    adapter.embed(["same"])
    adapter.embed_query("same")
    adapter.embed_query("same")
    assert len(transport.calls) == 3


def test_corrupted_cache_vector_is_recomputed_not_returned(tmp_path):
    import sqlite3

    from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache

    cache = SQLiteEmbeddingCache(tmp_path / "cache.sqlite3")
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), transport=transport, cache=cache)
    adapter.embed(["same"])
    with sqlite3.connect(cache.path) as db:
        db.execute("UPDATE vectors SET vector='[123,123]'")
    assert adapter.embed(["same"]) == [[4.0, 1.0]]
    assert len(transport.calls) == 2
