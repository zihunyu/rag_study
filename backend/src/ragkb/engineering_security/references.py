"""Persistent subject-bound opaque HMAC citation references."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from pydantic import SecretStr


class ReferenceTokenError(ValueError):
    pass


class ReferenceStorePort(Protocol):
    def save(self, opaque_id: str, record: dict[str, Any]) -> None: ...

    def get(self, opaque_id: str) -> dict[str, Any] | None: ...

    def revoke_document(self, document_id: str) -> int: ...


class HMACReferenceSigner:
    revision = "hmac-reference:g3-v2"

    def __init__(
        self,
        key: SecretStr | Mapping[str, SecretStr],
        store: ReferenceStorePort,
        *,
        active_kid: str = "local-v1",
        ttl_seconds: int = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        supplied = {active_kid: key} if isinstance(key, SecretStr) else dict(key)
        keys = {kid: secret.get_secret_value().encode() for kid, secret in supplied.items()}
        if (
            not active_kid
            or active_kid not in keys
            or any(not kid or len(value) < 16 for kid, value in keys.items())
            or ttl_seconds < 1
        ):
            raise ValueError("reference signing key and TTL are invalid")
        self._keys = keys
        self._active_kid = active_kid
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    @staticmethod
    def _encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _token(self, record: dict[str, Any]) -> str:
        opaque_id = secrets.token_urlsafe(24)
        body = opaque_id.encode()
        signature = hmac.new(self._keys[self._active_kid], body, hashlib.sha256).digest()
        self._store.save(opaque_id, record)
        return f"{self._active_kid}.{opaque_id}.{self._encode(signature)}"

    def source_url(
        self,
        run_id: str,
        evidence_id: str,
        tenant_id: str,
        user_id: str,
        document_id: str,
    ) -> str:
        expires = int(self._clock()) + self._ttl_seconds
        subject = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "run_id": run_id,
            "expires_at": expires,
        }
        run_token = self._token({"kind": "run", **subject})
        evidence_token = self._token(
            {
                "kind": "evidence",
                **subject,
                "evidence_id": evidence_id,
                "document_id": document_id,
            }
        )
        return f"/api/v1/rag-runs/{run_token}/evidence/{evidence_token}/source"

    def _verify(
        self,
        token: str,
        expected_kind: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        try:
            parts = token.split(".")
            if len(parts) == 3:
                kid, opaque_id, encoded_signature = parts
            elif len(parts) == 2:
                kid = self._active_kid
                opaque_id, encoded_signature = parts
            else:
                raise ValueError("invalid reference token parts")
            key = self._keys.get(kid)
            if key is None:
                raise ValueError("unknown reference key ID")
            body = opaque_id.encode()
            padding = "=" * (-len(encoded_signature) % 4)
            signature = base64.urlsafe_b64decode(encoded_signature + padding)
            expected = hmac.new(key, body, hashlib.sha256).digest()
        except Exception as error:
            raise ReferenceTokenError("REFERENCE_TOKEN_INVALID") from error
        if not hmac.compare_digest(signature, expected):
            raise ReferenceTokenError("REFERENCE_TOKEN_INVALID")
        record = self._store.get(opaque_id)
        if (
            not isinstance(record, dict)
            or record.get("token_kind", record.get("kind")) != expected_kind
            or str(record.get("tenant_id")) != tenant_id
            or str(record.get("user_id")) != user_id
            or bool(record.get("revoked"))
        ):
            raise ReferenceTokenError("REFERENCE_TOKEN_FORBIDDEN")
        if int(record.get("expires_at", 0)) < int(self._clock()):
            raise ReferenceTokenError("REFERENCE_TOKEN_EXPIRED")
        return record

    def resolve(
        self, run_token: str, evidence_token: str, tenant_id: str, user_id: str
    ) -> tuple[str, str]:
        run = self._verify(run_token, "run", tenant_id, user_id)
        evidence = self._verify(evidence_token, "evidence", tenant_id, user_id)
        if evidence.get("run_id") != run.get("run_id"):
            raise ReferenceTokenError("REFERENCE_TOKEN_MISMATCH")
        return str(run["run_id"]), str(evidence["evidence_id"])

    def revoke_document(self, document_id: str) -> int:
        return self._store.revoke_document(document_id)
