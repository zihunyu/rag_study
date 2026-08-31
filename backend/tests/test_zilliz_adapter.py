from __future__ import annotations

from pathlib import Path

from ragkb.adapters.zilliz import ZillizCloudAdapter
from ragkb.config import load_env


def test_zilliz_adapter_uses_uri_token_database_without_exposing_token(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret = "ZILLIZ_SECRET_MUST_NOT_LEAK"  # noqa: S105
    env_file.write_text(
        "\n".join(
            (
                "ZILLIZ_CLOUD_URI=https://cluster.cn-north.vectordb.zilliz.com.cn:19530",
                f"ZILLIZ_CLOUD_TOKEN={secret}",
                "ZILLIZ_CLOUD_DATABASE=default",
            )
        ),
        encoding="utf-8",
    )
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={})
    assert loaded.settings is not None
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    adapter = ZillizCloudAdapter(loaded.settings, client_factory=factory)
    adapter.connect()

    assert captured["uri"].startswith("https://")
    assert captured["token"] == secret
    assert captured["db_name"] == "default"
    assert secret not in str(adapter.safe_status())
    assert adapter.safe_status()["real_connection_attempted"] is False
