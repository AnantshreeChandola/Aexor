"""
CrossEncoderReranker — ONNX Cross-Encoder for Tier 2 Reranking

Loads an ONNX cross-encoder model (ms-marco-MiniLM-L-6-v2, ~80MB) and
scores (query, tool_description) pairs, returning the top_k most relevant
tools sorted by relevance score.

All inference runs locally via ONNX Runtime — $0 external API cost.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_TOKENIZER_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MAX_TOKENS = 512


class CrossEncoderReranker:
    """ONNX cross-encoder for reranking (query, tool_description) pairs."""

    def __init__(self, model_path: str) -> None:
        """Load ONNX cross-encoder model and tokenizer.

        Args:
            model_path: Path to the cross-encoder ONNX model file.

        Raises:
            RuntimeError: If model or tokenizer cannot be loaded.
        """
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(model_path)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load cross-encoder ONNX model at {model_path}: {exc}"
            ) from exc

        try:
            from tokenizers import Tokenizer

            self._tokenizer = Tokenizer.from_pretrained(_TOKENIZER_ID)
            self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)
            self._tokenizer.enable_padding(length=_MAX_TOKENS)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load cross-encoder tokenizer: {exc}"
            ) from exc

        logger.info(
            "cross_encoder_initialized",
            extra={"model_path": model_path},
        )

    def rerank(
        self,
        query: str,
        candidates: list[Any],
        top_k: int = 5,
    ) -> list[tuple[Any, float]]:
        """Score (query, tool.description) pairs and return top_k sorted results.

        Args:
            query: The intent text to match against.
            candidates: List of ToolDefinition objects with .description attribute.
            top_k: Maximum number of results to return.

        Returns:
            List of (ToolDefinition, score) tuples sorted by descending score.
        """
        if not candidates:
            return []

        # Build (query, description) pairs for the cross-encoder
        descriptions = [
            getattr(c, "description", "") or "" for c in candidates
        ]

        # Tokenize all pairs as [CLS] query [SEP] description [SEP]
        pairs = [(query, desc) for desc in descriptions]
        encodings = self._tokenizer.encode_batch(
            [f"{q} [SEP] {d}" for q, d in pairs]
        )

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array(
            [e.attention_mask for e in encodings], dtype=np.int64
        )
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        # Mark tokens after [SEP] as token_type_id=1 for segment B
        for i, encoding in enumerate(encodings):
            sep_positions = [
                j for j, tid in enumerate(encoding.ids)
                if tid == self._tokenizer.token_to_id("[SEP]")
            ]
            if len(sep_positions) >= 1:
                first_sep = sep_positions[0]
                token_type_ids[i, first_sep + 1:] = 1

        # Run ONNX inference
        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # Output shape: (batch, num_classes) or (batch, 1) — take logit for relevance
        logits = outputs[0]
        if logits.ndim == 2 and logits.shape[1] == 1:
            scores = logits[:, 0]
        elif logits.ndim == 2:
            # Multi-class: take second class (relevant) logit
            scores = logits[:, -1]
        else:
            scores = logits

        # Pair candidates with scores and sort
        scored = list(zip(candidates, scores.tolist()))
        scored.sort(key=lambda x: -x[1])

        return scored[:top_k]
