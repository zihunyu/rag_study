"""Pinned tokenizer loading and content checks shared by preflight and runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_PRODUCTION_PROBES = (
    "设备在常温条件下正常运行并应遵守维护保养规定" * 20,
    "Warranty excludes accidental damage. Model AX-2026 uses 480 W at 25 degrees. " * 8,
    "型号|额定功率|容量\n甲设备|480 W|960 Wh\n乙设备|960 W|1920 Wh\n" * 8,
    "繁體中文、日本語、한국어 café naïve résumé 😀🧪𠮷野家" * 8,
)


class TokenizerArtifact:
    """Load the exact hashed bytes, never a tokenizer's saved truncation policy."""

    def __init__(self, path: Path, expected_sha256: str, tokenizer_id: str) -> None:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError("TOKENIZER_ARTIFACT_MISSING")
        data = resolved.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not expected_sha256 or digest != expected_sha256.casefold():
            raise ValueError("TOKENIZER_ARTIFACT_SHA256_MISMATCH")
        if not tokenizer_id.strip():
            raise ValueError("TOKENIZER_ID_REQUIRED")
        from tokenizers import Tokenizer

        try:
            specification = json.loads(data)
            self._tokenizer = Tokenizer.from_str(data.decode("utf-8"))
            model = specification["model"]
            self._model_type = model["type"]
            unknown = model.get("unk_token")
            self._unknown_id = (
                self._tokenizer.token_to_id(unknown) if unknown else model.get("unk_id")
            )
        except Exception as error:
            # Parser exceptions may contain file contents; expose only a stable code.
            raise ValueError("TOKENIZER_ARTIFACT_INVALID") from error
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        self._production = False
        self.revision = f"{tokenizer_id}:{digest[:16]}:untruncated-v2"

    def validate_for_production(self) -> None:
        """Reject lossy/tiny test vocabularies independently of their path or label.

        These are suitability checks, not proof of token parity with a hosted model.
        """
        if (
            self._model_type not in {"BPE", "WordPiece", "Unigram"}
            or self._tokenizer.get_vocab_size(with_added_tokens=False) < 1000
        ):
            raise ValueError("TOKENIZER_PRODUCTION_UNSUITABLE")
        for text in _PRODUCTION_PROBES:
            encoded = self._tokenizer.encode(text, add_special_tokens=False)
            if (
                (self._unknown_id is not None and self._unknown_id in encoded.ids)
                or len(encoded.ids) < len(text.strip()) / 16
                or not self._covers_text(text, encoded.offsets)
            ):
                raise ValueError("TOKENIZER_PRODUCTION_UNSUITABLE")
        self._production = True

    @staticmethod
    def _covers_text(text: str, offsets: list[tuple[int, int]]) -> bool:
        covered = 0
        for start, end in offsets:
            if start < 0 or end > len(text) or end < start or text[covered:start].strip():
                return False
            covered = max(covered, end)
        return not text[covered:].strip()

    def spans(self, text: str) -> tuple[tuple[int, int], ...]:
        encoded = self._tokenizer.encode(text, add_special_tokens=False)
        if self._production and (
            (self._unknown_id is not None and self._unknown_id in encoded.ids)
            or not self._covers_text(text, encoded.offsets)
        ):
            raise ValueError("TOKENIZER_INPUT_NOT_REPRESENTABLE")
        return tuple((start, end) for start, end in encoded.offsets if end > start)
