"""Regression tests for finish.py's ffmpeg-filter escaping and path validation
(2026-07-22, per rhobear-reviews HIGH findings on PR #35). No ffmpeg binary
required -- these test pure string/path logic, not subprocess calls.
Run directly: `python director-rig/tests/test_finish.py`
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finish import _ff_text, _safe_input_path  # noqa: E402


def test_ff_text_escapes_single_quote():
    # A single quote would otherwise close drawtext's text='...' early.
    assert "'" not in _ff_text("it's a demo").replace(r"'\''", "")


def test_ff_text_escapes_colon_and_percent():
    out = _ff_text("Save 50%: today only")
    assert "\\:" in out
    assert "\\%" in out


def test_ff_text_escapes_backslash():
    # NOT vacuous: verified against two broken variants of _ff_text (strip
    # the backslash entirely; no-op the backslash replace) and both fail
    # this assertion (count drops to 0), confirming it actually detects a
    # regression rather than always passing. Exact-value check, not just a
    # count, so there's no ambiguity about what "escaped" means here.
    assert _ff_text(r"C:\path") == "C\\:\\\\path"
    assert _ff_text(r"C:\path").count("\\\\") == 1


def test_safe_input_path_rejects_protocol_prefix():
    # Only a leading bare scheme (letters/digits/+.- then ':', no leading
    # path separator) is rejected -- these all match that shape.
    for bad in ("concat:a.mp4|b.mp4", "http://evil/x.png", "pipe:0", "tcp://host:1234"):
        try:
            _safe_input_path(bad, "watermark")
        except SystemExit:
            continue
        raise AssertionError(f"expected SystemExit for {bad!r}")


def test_safe_input_path_allows_midname_colon():
    # A colon that isn't a leading protocol scheme is a legal Linux
    # filename character (e.g. an asset export named "logo_v2:final.png")
    # and must NOT be rejected -- narrowed from an earlier blanket
    # any-colon rejection per rhobear-reviews feedback on PR #35.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "logo_v2:final.png"
        p.write_bytes(b"\x89PNG")
        resolved = _safe_input_path(str(p), "watermark")
        assert Path(resolved).is_file()


def test_safe_input_path_rejects_missing_file():
    try:
        _safe_input_path("/definitely/not/a/real/file.png", "watermark")
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit for a nonexistent file")


def test_safe_input_path_accepts_real_file():
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        resolved = _safe_input_path(f.name, "watermark")
        assert Path(resolved).is_file()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} passed")
