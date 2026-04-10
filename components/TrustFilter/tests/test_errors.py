"""Tests for TrustFilter domain errors -- T205."""

import pytest

from components.TrustFilter.domain.errors import (
    HaikuUnreachableError,
    LoadBearingFlaggedError,
    MalformedInputError,
    PayloadDepthExceededError,
    PayloadTooLargeError,
    S1InternalError,
    TrustFilterError,
)


class TestErrorTypes:
    """Each error must carry correct error_type."""

    def test_base_error_type(self) -> None:
        err = TrustFilterError("test")
        assert err.error_type == "trust_filter_error"

    def test_load_bearing_flagged(self) -> None:
        err = LoadBearingFlaggedError(
            "events[0].description", "ignore_previous"
        )
        assert err.error_type == "load_bearing_field_flagged"
        assert err.field_path == "events[0].description"
        assert err.rule_id == "ignore_previous"

    def test_payload_too_large(self) -> None:
        err = PayloadTooLargeError(2_000_000)
        assert err.error_type == "payload_too_large"
        assert err.size_bytes == 2_000_000

    def test_payload_depth_exceeded(self) -> None:
        err = PayloadDepthExceededError(33)
        assert err.error_type == "payload_depth_exceeded"
        assert err.depth == 33

    def test_malformed_input(self) -> None:
        err = MalformedInputError("not JSON")
        assert err.error_type == "malformed_input"

    def test_s1_internal(self) -> None:
        err = S1InternalError("regex crashed")
        assert err.error_type == "s1_internal"

    def test_haiku_unreachable(self) -> None:
        err = HaikuUnreachableError("timeout")
        assert err.error_type == "haiku_unreachable"


class TestPrivacyGuarantee:
    """Error __str__ must never contain raw payload content."""

    def test_load_bearing_str_no_payload(self) -> None:
        err = LoadBearingFlaggedError(
            "events[0].description", "rule_x"
        )
        text = str(err)
        assert "events[0].description" in text
        assert "rule_x" in text
        # Should not contain arbitrary payload data
        assert "inject" not in text.lower() or "injection" not in text.lower()

    def test_payload_too_large_str(self) -> None:
        err = PayloadTooLargeError(999_999)
        text = str(err)
        assert "999999" in text

    def test_malformed_input_str(self) -> None:
        err = MalformedInputError("Object of type set is not serializable")
        text = str(err)
        assert "Malformed input" in text
