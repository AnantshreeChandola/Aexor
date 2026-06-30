"""
TrustFilter domain errors -- LLD Section 5.3.

All errors carry an ``error_type`` attribute for StepResult mapping.
Custom ``__str__`` methods never include payload content (privacy).
"""


class TrustFilterError(Exception):
    """Base error for the TrustFilter component."""

    error_type: str = "trust_filter_error"

    def __str__(self) -> str:
        return f"[{self.error_type}] {self.args[0] if self.args else ''}"


class LoadBearingFlaggedError(TrustFilterError):
    """A load-bearing field was flagged by a scan rule."""

    error_type = "load_bearing_field_flagged"

    def __init__(self, field_path: str, rule_id: str) -> None:
        self.field_path = field_path
        self.rule_id = rule_id
        super().__init__(
            f"Load-bearing field '{field_path}' "
            f"flagged by rule '{rule_id}'"
        )


class PayloadTooLargeError(TrustFilterError):
    """Payload exceeds MAX_PAYLOAD_BYTES (1 MB)."""

    error_type = "payload_too_large"

    def __init__(self, size_bytes: int) -> None:
        self.size_bytes = size_bytes
        super().__init__(
            f"Payload size {size_bytes}B exceeds limit"
        )


class PayloadDepthExceededError(TrustFilterError):
    """JSON nesting exceeds MAX_DEPTH (32)."""

    error_type = "payload_depth_exceeded"

    def __init__(self, depth: int | None = None) -> None:
        self.depth = depth
        msg = "Payload depth exceeded"
        if depth is not None:
            msg = f"Payload depth {depth} exceeds limit"
        super().__init__(msg)


class MalformedInputError(TrustFilterError):
    """Payload is not JSON-serializable."""

    error_type = "malformed_input"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(
            f"Malformed input: {reason}" if reason else "Malformed input"
        )


class S1InternalError(TrustFilterError):
    """S1 regex engine or rule-pack load failure.

    Internal only -- caught by FilterService and degrades to S2-only.
    Never propagated to caller.
    """

    error_type = "s1_internal"


class HaikuUnreachableError(TrustFilterError):
    """S2 Haiku judge is unreachable (timeout, rate limit, API error)."""

    error_type = "haiku_unreachable"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(
            f"Haiku unreachable: {reason}" if reason else "Haiku unreachable"
        )
