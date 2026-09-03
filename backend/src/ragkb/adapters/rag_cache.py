"""Production Redis cache for citation-verified answer drafts."""

from __future__ import annotations

from redis.exceptions import RedisError

from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.application.qa import verified_answer_cache_key
from ragkb.domain.rag import DraftAnswer, EvidencePackage


class RedisVerifiedAnswerCache:
    revision = "redis-verified-answer-cache:v1"

    def __init__(self, redis: RedisCacheRateLimitAdapter, *, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    def get(self, package: EvidencePackage) -> DraftAnswer | None:
        try:
            value = self.redis.get_json("verified-answer", verified_answer_cache_key(package))
        except (RedisError, ValueError):
            return None
        if value is None:
            return None
        answer = value.get("answer")
        citation_ids = value.get("citation_ids")
        if not isinstance(answer, str) or not isinstance(citation_ids, list):
            return None
        return DraftAnswer(answer, tuple(map(str, citation_ids)))

    def put(self, package: EvidencePackage, draft: DraftAnswer) -> None:
        try:
            self.redis.set_json(
                "verified-answer",
                verified_answer_cache_key(package),
                {"answer": draft.text, "citation_ids": list(draft.citation_ids)},
                self.ttl_seconds,
            )
        except RedisError:
            return
