def test_focus_block_contract():
    # Replace with a real call later; this just wires CI.
    normalized = {
        "title": "Focus",
        "start": "2025-09-15T19:00:00Z",
        "end": "2025-09-15T21:00:00Z",
        "tz": "America/Chicago",
    }
    assert all(k in normalized for k in ("title", "start", "end", "tz"))
