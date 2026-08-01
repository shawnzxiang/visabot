from visabot.notifier import CHUNK_TARGET, chunk_body, pushover_params


def test_short_body_single_chunk():
    assert chunk_body("hello\nworld") == ["hello\nworld"]


def test_long_body_splits_on_lines():
    lines = [f"line {i:03d} " + "x" * 40 for i in range(40)]
    body = "\n".join(lines)
    parts = chunk_body(body)
    assert len(parts) >= 2
    for p in parts:
        assert len(p) <= CHUNK_TARGET
    # no content lost, split only at line boundaries
    rejoined = "\n".join(p for p in parts)
    assert rejoined.replace("\n", "") == body.replace("\n", "")


def test_oversize_single_line_hard_split():
    body = "y" * 2500
    parts = chunk_body(body)
    assert all(len(p) <= CHUNK_TARGET for p in parts)
    assert "".join(parts) == body


def test_priority_2_includes_retry_expire():
    p = pushover_params("t", "m", 2)
    assert p["priority"] == "2"
    assert p["retry"] == "30"
    assert p["expire"] == "3600"


def test_lower_priorities_omit_retry_expire():
    for prio in (0, 1):
        p = pushover_params("t", "m", prio)
        assert "retry" not in p and "expire" not in p


def test_title_truncated_to_250():
    p = pushover_params("T" * 400, "m", 0)
    assert len(p["title"]) == 250
