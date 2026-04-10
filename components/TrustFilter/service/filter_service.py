"""
FilterService -- stateless sanitizer orchestrator.

LLD Sections 6.4, 7.1-7.4. Ties S1 + S2 + S3 together into
the main scan() pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Final

from components.TrustFilter.adapters.regex_scanner import (
    RegexScanner,
)
from components.TrustFilter.domain.errors import (
    HaikuUnreachableError,
    LoadBearingFlaggedError,
    MalformedInputError,
    PayloadTooLargeError,
    S1InternalError,
)
from components.TrustFilter.domain.models import (
    RuleHit,
    S2Result,
    ScanContext,
)
from components.TrustFilter.domain.tree_walker import (
    MAX_PAYLOAD_BYTES,
    JsonTreeWalker,
)
from shared.schemas.sanitized_payload import SanitizedPayload
from shared.schemas.trust import Verdict

logger = logging.getLogger(__name__)

VERDICT_PARANOIA: dict[str, int] = {
    "clean": 0,
    "suspicious": 1,
    "injection": 2,
}


class FilterService:
    """Stateless sanitizer. Orchestrates S1 -> S2 -> S3."""

    SCANNER_VERSION: Final[str] = "trust_filter@0.1.0"

    def __init__(
        self,
        regex_scanner: RegexScanner,
        haiku_adapter: Any,
        tree_walker: JsonTreeWalker | None = None,
    ) -> None:
        self._s1 = regex_scanner
        self._s2 = haiku_adapter
        self._walker = tree_walker or JsonTreeWalker()

    async def scan(
        self,
        raw_payload: dict | list | str | None,
        *,
        load_bearing_fields: list[str] | None = None,
        strict_mode: bool = False,
        plan_id: str,
        step_number: int,
        trace_id: str,
    ) -> SanitizedPayload:
        """Run the S1 -> S2 -> S3 pipeline.

        Args:
            raw_payload: Upstream MCP tool response.
            load_bearing_fields: Dotted paths that must not be
                stripped.
            strict_mode: If True, treat suspicious as injection.
            plan_id: For log correlation.
            step_number: For log correlation.
            trace_id: For log correlation.

        Returns:
            SanitizedPayload with verdict and stripped fields.

        Raises:
            LoadBearingFlaggedError: Load-bearing field flagged.
            PayloadTooLargeError: Payload > 1 MB.
            PayloadDepthExceededError: JSON depth > 32.
            MalformedInputError: Not JSON-serializable.
        """
        start = time.monotonic()
        ctx = ScanContext(
            plan_id=plan_id,
            step_number=step_number,
            trace_id=trace_id,
            load_bearing_fields=set(
                load_bearing_fields or []
            ),
            strict_mode=strict_mode,
        )

        logger.info(
            "scan_start",
            extra={
                "component": "trust_filter",
                "op": "scan",
                "plan_id": ctx.plan_id,
                "step": ctx.step_number,
                "trace_id": ctx.trace_id,
                "load_bearing_count": len(
                    ctx.load_bearing_fields
                ),
                "strict_mode": ctx.strict_mode,
            },
        )

        # Guard: size, malformed
        self._check_payload_limits(raw_payload)

        # S1: collect rule hits across all string fields
        s1_hits = self._run_s1(raw_payload, ctx)

        # Early exit: S1 found nothing -> skip S2
        if not s1_hits:
            result = self._build_payload(
                raw_payload,
                stripped=set(),
                verdict="clean",
                confidence=0.99,
                scanner_degraded=False,
                ctx=ctx,
            )
            self._log_complete(ctx, result, start)
            return result

        # S2: ask Haiku for a second opinion
        s2_result, degraded = await self._run_s2(
            raw_payload, s1_hits, ctx
        )

        # Combine verdicts: pick more paranoid
        final_verdict, final_conf = self._combine_verdicts(
            s1_hits, s2_result
        )

        # Decide stripped fields (check load-bearing)
        stripped = self._select_fields_to_strip(
            s1_hits, final_verdict, ctx, strict_mode
        )

        # S3: build payload with strips applied
        result = self._build_payload(
            raw_payload,
            stripped=stripped,
            verdict=final_verdict,
            confidence=final_conf,
            scanner_degraded=degraded,
            ctx=ctx,
        )
        self._log_complete(ctx, result, start)
        return result

    # ---------------------------------------------------------------
    # Payload limits
    # ---------------------------------------------------------------

    @staticmethod
    def _check_payload_limits(
        payload: Any,
    ) -> None:
        """Check JSON-serializable and size."""
        try:
            serialized = json.dumps(
                payload, ensure_ascii=False
            )
        except (TypeError, ValueError) as exc:
            raise MalformedInputError(str(exc)) from exc
        size = len(serialized.encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise PayloadTooLargeError(size)

    # ---------------------------------------------------------------
    # S1 run
    # ---------------------------------------------------------------

    def _run_s1(
        self,
        payload: Any,
        ctx: ScanContext,
    ) -> list[RuleHit]:
        """Collect S1 regex hits across all string fields."""
        hits: list[RuleHit] = []
        fields_scanned = 0
        try:
            for path, value in self._walker.walk(payload):
                fields_scanned += 1
                hits.extend(
                    self._s1.scan_string(value, path)
                )
        except S1InternalError:
            logger.exception(
                "s1_internal_error",
                extra={
                    "component": "trust_filter",
                    "op": "s1",
                    "plan_id": ctx.plan_id,
                    "step": ctx.step_number,
                },
            )
            return []

        logger.info(
            "s1_scan_complete",
            extra={
                "component": "trust_filter",
                "op": "s1",
                "plan_id": ctx.plan_id,
                "step": ctx.step_number,
                "fields_scanned": fields_scanned,
                "hit_count": len(hits),
            },
        )
        return hits

    # ---------------------------------------------------------------
    # S2 run (fail-open with escalation)
    # ---------------------------------------------------------------

    async def _run_s2(
        self,
        payload: Any,
        s1_hits: list[RuleHit],
        ctx: ScanContext,
    ) -> tuple[S2Result | None, bool]:
        """Run S2 Haiku judge. Returns (result, degraded)."""
        try:
            payload_text = self._serialize_for_judge(
                payload
            )
            s1_rule_ids = [h.rule_id for h in s1_hits]
            result = await self._s2.classify(
                payload_text, s1_rule_ids
            )
            logger.info(
                "s2_classify_complete",
                extra={
                    "component": "trust_filter",
                    "op": "s2",
                    "plan_id": ctx.plan_id,
                    "step": ctx.step_number,
                    "s2_verdict": result.verdict,
                    "s2_confidence": result.confidence,
                },
            )
            return result, False
        except HaikuUnreachableError as exc:
            logger.warning(
                "s2_unreachable_degrading",
                extra={
                    "component": "trust_filter",
                    "op": "s2",
                    "plan_id": ctx.plan_id,
                    "step": ctx.step_number,
                    "reason": str(exc),
                },
            )
            return None, True

    @staticmethod
    def _serialize_for_judge(payload: Any) -> str:
        """Serialize payload for S2 input (truncated to 16KB)."""
        try:
            text = json.dumps(
                payload, ensure_ascii=False, default=str
            )
        except (TypeError, ValueError):
            text = str(payload)
        return text[:16_000]

    # ---------------------------------------------------------------
    # Verdict combination
    # ---------------------------------------------------------------

    def _combine_verdicts(
        self,
        s1_hits: list[RuleHit],
        s2_result: S2Result | None,
    ) -> tuple[Verdict, float]:
        """Pick the more paranoid verdict."""
        s1_verdict, s1_conf = self._s1.aggregate(s1_hits)

        if s2_result is None:
            return s1_verdict, s1_conf

        s1_rank = VERDICT_PARANOIA[s1_verdict]
        s2_rank = VERDICT_PARANOIA[s2_result.verdict]

        if s2_rank > s1_rank:
            return s2_result.verdict, s2_result.confidence
        if s1_rank > s2_rank:
            return s1_verdict, s1_conf
        # Same verdict -> average confidence
        avg = (s1_conf + s2_result.confidence) / 2
        return s1_verdict, avg

    # ---------------------------------------------------------------
    # Field stripping
    # ---------------------------------------------------------------

    def _select_fields_to_strip(
        self,
        s1_hits: list[RuleHit],
        final_verdict: Verdict,
        ctx: ScanContext,
        strict_mode: bool,
    ) -> set[str]:
        """Decide which fields to strip.

        Raises:
            LoadBearingFlaggedError: If a load-bearing field
                is in the strip set.
        """
        if final_verdict == "clean":
            return set()
        if final_verdict == "suspicious" and not strict_mode:
            return set()

        # injection, OR suspicious + strict
        to_strip: set[str] = set()
        for hit in s1_hits:
            if hit.severity in {"med", "high"}:
                to_strip.add(hit.field_path)

        # Check load-bearing fields
        for path in to_strip:
            if path in ctx.load_bearing_fields:
                first_rule = next(
                    (h.rule_id for h in s1_hits
                     if h.field_path == path),
                    "unknown",
                )
                raise LoadBearingFlaggedError(
                    path, first_rule
                )

        return to_strip

    # ---------------------------------------------------------------
    # Payload building (S3)
    # ---------------------------------------------------------------

    def _build_payload(
        self,
        raw_payload: Any,
        *,
        stripped: set[str],
        verdict: Verdict,
        confidence: float,
        scanner_degraded: bool,
        ctx: ScanContext,
    ) -> SanitizedPayload:
        """Build shape-preserving SanitizedPayload."""
        original_shape = self._walker.apply_strips(
            raw_payload, stripped
        )

        for path in sorted(stripped):
            logger.info(
                "field_stripped",
                extra={
                    "component": "trust_filter",
                    "op": "s3",
                    "plan_id": ctx.plan_id,
                    "step": ctx.step_number,
                    "field_path": path,
                },
            )

        return SanitizedPayload(
            original_shape=original_shape,
            stripped_fields=sorted(stripped),
            trust_verdict=verdict,
            confidence=confidence,
            scanner_degraded=scanner_degraded,
            scanner_version=self.SCANNER_VERSION,
            scanned_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _log_complete(
        ctx: ScanContext,
        result: SanitizedPayload,
        start: float,
    ) -> None:
        """Log scan completion."""
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "scan_complete",
            extra={
                "component": "trust_filter",
                "op": "scan",
                "plan_id": ctx.plan_id,
                "step": ctx.step_number,
                "final_verdict": result.trust_verdict,
                "stripped_count": len(result.stripped_fields),
                "total_duration_ms": duration_ms,
                "scanner_degraded": result.scanner_degraded,
            },
        )


def create_filter_service(
    haiku_adapter: Any | None = None,
    regex_scanner: RegexScanner | None = None,
) -> FilterService:
    """Create FilterService with DI-injected dependencies.

    Args:
        haiku_adapter: S2 adapter (default: HaikuJudgeAdapterImpl).
        regex_scanner: S1 scanner (default: RegexScanner()).

    Returns:
        Configured FilterService.
    """
    if regex_scanner is None:
        regex_scanner = RegexScanner()

    if haiku_adapter is None:
        from components.TrustFilter.adapters.haiku_judge import (
            HaikuJudgeAdapterImpl,
        )
        haiku_adapter = HaikuJudgeAdapterImpl()

    return FilterService(
        regex_scanner=regex_scanner,
        haiku_adapter=haiku_adapter,
    )
