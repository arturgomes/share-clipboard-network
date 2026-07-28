"""Unit tests for the Linux password-manager-hint MIME target skip logic
(AC5). Exercises only the pure helper in
vendor/ClipCascade_Desktop/src/clipboard/sensitive.py -- no GTK/gi import,
so this runs on macOS with no GTK installed.
"""

from clipboard.sensitive import should_skip_mime_targets


def test_pm_hint_present_skips():
    assert (
        should_skip_mime_targets(["text/plain", "x-kde-passwordManagerHint"]) is True
    )


def test_pm_hint_alongside_other_targets_skips():
    assert (
        should_skip_mime_targets(
            ["UTF8_STRING", "STRING", "x-kde-passwordManagerHint", "TARGETS"]
        )
        is True
    )


def test_normal_text_targets_do_not_skip():
    assert (
        should_skip_mime_targets(["UTF8_STRING", "STRING", "TEXT", "COMPOUND_TEXT"])
        is False
    )


def test_normal_mime_targets_do_not_skip():
    assert should_skip_mime_targets(["text/plain;charset=utf-8", "image/png"]) is False


def test_empty_list_does_not_skip():
    # Successfully inspected, nothing sensitive found -- send normally.
    assert should_skip_mime_targets([]) is False


def test_none_fails_closed_and_skips():
    # AC5 fail-closed semantics: None means "could not inspect" (xclip/
    # wl-paste/GTK failure, an exception, etc), which must be treated as
    # sensitive, never as safe-to-send. This is the opposite of the old
    # fail-open behavior.
    assert should_skip_mime_targets(None) is True


def test_case_variation_does_not_match():
    # The real-world flag is "x-kde-passwordManagerHint" (mixed case). A
    # differently-cased variant must NOT be treated as a match -- the check
    # must not silently become case-insensitive.
    assert should_skip_mime_targets(["x-kde-passwordmanagerhint"]) is False
    assert should_skip_mime_targets(["X-KDE-PASSWORDMANAGERHINT"]) is False


def test_exact_real_world_casing_matches():
    assert should_skip_mime_targets(["x-kde-passwordManagerHint"]) is True
