"""Regression test for the adjusted() crash fixed 2026-07-22.

adjusted() maps a shot-list act index to a live engine step index. It used
to raise KeyError/ValueError whenever an act step had no live engine step
(selector timeout, or the step got trimmed as a stray SPA interaction) --
observed as job 08a1b1ad8396 on a rhobear.ai walkthrough (KeyError: 2).
Run directly: `python director-rig/tests/test_adjusted.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from film import _adjusted as adjusted  # noqa: E402


def test_missing_act_returns_none():
    # act 2's demo.act call never completed -> no key in act_to_engine at all.
    # This is the exact shape of the original crash (KeyError: 2).
    act_to_engine = {0: 0, 1: 1}
    kept = [0, 1]
    assert adjusted(2, act_to_engine, kept) is None


def test_trimmed_stray_returns_none():
    # act 2 got an engine stepIndex, but that engine step was later trimmed
    # as a stray SPA interaction, so it's absent from `kept`.
    act_to_engine = {0: 0, 1: 1, 2: 5}
    kept = [0, 1]
    assert adjusted(2, act_to_engine, kept) is None


def test_resolves_normally_when_present():
    act_to_engine = {0: 0, 1: 1, 2: 2}
    kept = [0, 1, 2]
    assert adjusted(2, act_to_engine, kept) == 2


def test_resolves_after_earlier_strays_trimmed():
    # act 2's engine step (4) survived trimming but shifted position in
    # `kept` because earlier stray steps (1, 3) were dropped.
    act_to_engine = {0: 0, 2: 4}
    kept = [0, 2, 4]
    assert adjusted(2, act_to_engine, kept) == 2


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} passed")
