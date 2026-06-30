"""Tests for JsonTreeWalker -- T208, FR-006, FR-007, FR-010."""

import pytest

from components.TrustFilter.domain.errors import (
    PayloadDepthExceededError,
)
from components.TrustFilter.domain.tree_walker import (
    ALWAYS_SCAN_FIELD_NAMES,
    REDACTED_MARKER,
    JsonTreeWalker,
)


@pytest.fixture()
def walker() -> JsonTreeWalker:
    return JsonTreeWalker()


class TestBasicTraversal:
    def test_flat_dict(self, walker: JsonTreeWalker) -> None:
        payload = {"name": "Alice", "note": "hello"}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "name" in paths
        assert "note" in paths

    def test_nested_dict(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"a": {"b": {"c": "value"}}}
        pairs = list(walker.walk(payload))
        assert ("a.b.c", "value") in pairs

    def test_list_indexing(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"items": [{"note": "x"}, {"note": "y"}]}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "items[0].note" in paths
        assert "items[1].note" in paths

    def test_root_list(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = [{"text": "hello"}, {"text": "world"}]
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "[0].text" in paths
        assert "[1].text" in paths


class TestDepthLimit:
    def test_depth_exceeded(
        self, walker: JsonTreeWalker
    ) -> None:
        payload: dict = {"a": "leaf"}
        current = payload
        for _ in range(35):
            child: dict = {"nested": "leaf"}
            current["child"] = child
            current = child
        with pytest.raises(PayloadDepthExceededError):
            list(walker.walk(payload))


class TestStructuredFieldSkipping:
    def test_skip_id_field(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"event_id": "evt_123", "title": "Meeting"}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "event_id" not in paths
        assert "title" in paths

    def test_skip_at_field(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {
            "created_at": "2026-04-08T10:00:00Z",
            "note": "hi",
        }
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "created_at" not in paths
        assert "note" in paths

    def test_skip_email_value(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"sender": "alice@example.com"}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "sender" not in paths

    def test_skip_iso_date(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"start": "2026-04-08T10:00:00"}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "start" not in paths

    def test_skip_uuid(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {
            "ref": "c89f1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b"
        }
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "ref" not in paths

    def test_skip_url_field(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {
            "link_url": "https://example.com/page"
        }
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "link_url" not in paths

    def test_skip_numeric_string(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"count": "42"}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "count" not in paths


class TestAlwaysScanFields:
    """FR-007: ALWAYS_SCAN_FIELD_NAMES override."""

    @pytest.mark.parametrize("field", list(ALWAYS_SCAN_FIELD_NAMES))
    def test_always_scanned(
        self, walker: JsonTreeWalker, field: str
    ) -> None:
        payload = {field: "some text that looks normal"}
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert field in paths

    def test_description_scanned_even_if_url_like(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {
            "description": "https://example.com/event"
        }
        pairs = list(walker.walk(payload))
        paths = [p for p, _ in pairs]
        assert "description" in paths


class TestApplyStrips:
    """FR-010: shape-preserving strip."""

    def test_basic_strip(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {
            "a": {"b": [{"note": "bad", "id": "ok"}]}
        }
        result = walker.apply_strips(
            payload, {"a.b[0].note"}
        )
        assert result["a"]["b"][0]["note"] == REDACTED_MARKER
        assert result["a"]["b"][0]["id"] == "ok"

    def test_no_strips_preserves(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"x": "y"}
        result = walker.apply_strips(payload, set())
        assert result == payload
        # Must be a copy
        assert result is not payload

    def test_multiple_strips(
        self, walker: JsonTreeWalker
    ) -> None:
        payload = {"a": "bad1", "b": "bad2", "c": "ok"}
        result = walker.apply_strips(
            payload, {"a", "b"}
        )
        assert result["a"] == REDACTED_MARKER
        assert result["b"] == REDACTED_MARKER
        assert result["c"] == "ok"


class TestEmptyPayload:
    def test_empty_dict(
        self, walker: JsonTreeWalker
    ) -> None:
        pairs = list(walker.walk({}))
        assert pairs == []

    def test_none_payload(
        self, walker: JsonTreeWalker
    ) -> None:
        pairs = list(walker.walk(None))
        assert pairs == []

    def test_empty_string(
        self, walker: JsonTreeWalker
    ) -> None:
        pairs = list(walker.walk(""))
        assert pairs == []
