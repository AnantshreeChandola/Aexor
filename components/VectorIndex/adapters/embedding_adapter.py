"""
EmbeddingAdapter — ONNX Runtime Inference

Wraps ONNX Runtime for all-MiniLM-L6-v2 inference. Generates 384-dim
L2-normalized embeddings from text input. Tokenizes with HuggingFace
tokenizers library, truncates at 256 tokens.
"""

import logging

import numpy as np

from components.VectorIndex.domain.models import EmbeddingModelError

logger = logging.getLogger("vectorindex")

_MODEL_NAME = "all-MiniLM-L6-v2"
_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"
_MAX_TOKENS = 256
_EMBEDDING_DIM = 384


class EmbeddingAdapter:
    """Generate 384-dim embeddings using all-MiniLM-L6-v2 via ONNX Runtime."""

    def __init__(self, model_path: str) -> None:
        """Load ONNX model and tokenizer.

        Args:
            model_path: Path to ONNX model file.

        Raises:
            EmbeddingModelError: If model or tokenizer cannot be loaded.
        """
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(model_path)
        except Exception as exc:
            raise EmbeddingModelError(
                model_name=_MODEL_NAME,
                reason=f"Failed to load ONNX model at {model_path}: {exc}",
            )

        try:
            from tokenizers import Tokenizer

            self._tokenizer = Tokenizer.from_pretrained(_TOKENIZER_ID)
            self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)
            self._tokenizer.enable_padding(length=_MAX_TOKENS)
        except Exception as exc:
            raise EmbeddingModelError(
                model_name=_MODEL_NAME,
                reason=f"Failed to load tokenizer: {exc}",
            )

        logger.info(
            "embedding_adapter_initialized",
            extra={"model_path": model_path, "model_name": _MODEL_NAME},
        )

    def embed(self, text: str) -> list[float]:
        """Generate 384-dim embedding for a text string.

        Args:
            text: Input text (truncated at 256 tokens).

        Returns:
            List of 384 floats (L2-normalized embedding).
        """
        encoded = self._tokenizer.encode(text)
        input_ids = np.array([encoded.ids], dtype=np.int64)
        attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # Mean pooling over token embeddings (masked)
        token_embeddings = outputs[0]  # (1, seq_len, 384)
        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        summed = np.sum(token_embeddings * mask_expanded, axis=1)
        counts = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        mean_pooled = summed / counts

        # L2 normalize
        norm = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        normalized = mean_pooled / np.clip(norm, a_min=1e-9, a_max=None)

        return normalized[0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of input strings.

        Returns:
            List of 384-dim embeddings, one per input text.
        """
        if not texts:
            return []

        encodings = self._tokenizer.encode_batch(texts)

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array(
            [e.attention_mask for e in encodings],
            dtype=np.int64,
        )
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        token_embeddings = outputs[0]  # (batch, seq_len, 384)
        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        summed = np.sum(token_embeddings * mask_expanded, axis=1)
        counts = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        mean_pooled = summed / counts

        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        normalized = mean_pooled / np.clip(norms, a_min=1e-9, a_max=None)

        return [row.tolist() for row in normalized]
