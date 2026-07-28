"""Integration-ish tests for clipboard_monitor_mac's pasteboard-type
inspection wiring, using module-boundary mocks -- no real `pasteboard`
package or AppKit/pyobjc is installed (or needed) to run this file.

`clipboard_monitor_mac.py` does `import pasteboard` unconditionally at
module scope, so a fake `pasteboard` module is installed in sys.modules
before the module is first imported. `from AppKit import NSPasteboard`
happens lazily inside `_current_pasteboard_types()`, so AppKit is only
faked per-test via `builtins.__import__`/sys.modules, exactly the
module-boundary the function was designed around.

Covers both the pure `_current_pasteboard_types()` unit (fail-closed
semantics per finding F2) and full `_runner()` wiring (finding F1's
before+after TOCTOU re-check and finding F3's files_processed
mutual-exclusion invariant), by driving `_runner()` for exactly one poll
iteration via a `time.sleep` stub that clears the module's `_run` flag.
"""

import builtins
import sys
import types
from unittest.mock import Mock

import pytest


def _install_fake_pasteboard_module():
    """Install a minimal stand-in for the `pasteboard` PyPI package so
    `clipboard_monitor_mac` can be imported without it being installed."""
    if "pasteboard" in sys.modules:
        return

    fake = types.ModuleType("pasteboard")

    class PasteboardType:
        pass

    fake.PasteboardType = PasteboardType
    fake.HTML = PasteboardType()
    fake.PDF = PasteboardType()
    fake.PNG = PasteboardType()
    fake.RTF = PasteboardType()
    fake.String = PasteboardType()
    fake.TIFF = PasteboardType()
    fake.TabularText = PasteboardType()

    class Pasteboard:
        def __init__(self):
            pass

        def get_contents(self, *args, **kwargs):
            return None

        def set_contents(self, *args, **kwargs):
            return True

        def get_file_urls(self, *args, **kwargs):
            return None

    fake.Pasteboard = Pasteboard
    sys.modules["pasteboard"] = fake


@pytest.fixture
def clipboard_monitor_mac():
    _install_fake_pasteboard_module()
    import importlib

    return importlib.import_module("clipboard.clipboard_monitor_mac")


# --- _current_pasteboard_types() unit tests (fail-closed semantics, F2) ---


def test_current_pasteboard_types_reads_appkit_types(clipboard_monitor_mac, monkeypatch):
    class FakeGeneralPasteboard:
        def types(self):
            return ["public.utf8-plain-text", "org.nspasteboard.ConcealedType"]

    class FakeNSPasteboard:
        @staticmethod
        def generalPasteboard():
            return FakeGeneralPasteboard()

    fake_appkit = types.ModuleType("AppKit")
    fake_appkit.NSPasteboard = FakeNSPasteboard
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)

    result = clipboard_monitor_mac._current_pasteboard_types()

    assert result == ["public.utf8-plain-text", "org.nspasteboard.ConcealedType"]
    assert clipboard_monitor_mac.should_skip_pasteboard_types(result) is True


def test_current_pasteboard_types_none_types_is_empty_list(
    clipboard_monitor_mac, monkeypatch
):
    class FakeGeneralPasteboard:
        def types(self):
            return None

    class FakeNSPasteboard:
        @staticmethod
        def generalPasteboard():
            return FakeGeneralPasteboard()

    fake_appkit = types.ModuleType("AppKit")
    fake_appkit.NSPasteboard = FakeNSPasteboard
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)

    result = clipboard_monitor_mac._current_pasteboard_types()

    # Successfully inspected, nothing there -- normal, not fail-closed.
    assert result == []
    assert clipboard_monitor_mac.should_skip_pasteboard_types(result) is False


def test_current_pasteboard_types_degrades_and_fails_closed_when_appkit_unavailable(
    clipboard_monitor_mac, monkeypatch, caplog
):
    monkeypatch.setattr(clipboard_monitor_mac, "_inspection_unavailable_warned", False)
    monkeypatch.delitem(sys.modules, "AppKit", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "AppKit":
            raise ImportError("no module named AppKit")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("WARNING"):
        result = clipboard_monitor_mac._current_pasteboard_types()

    # None means "could not inspect" to the caller, which must fail CLOSED
    # (AC5, finding F2) -- never degrade to sending.
    assert result is None
    assert clipboard_monitor_mac.should_skip_pasteboard_types(result) is True
    assert any(
        "clipboard type inspection unavailable -- failing closed" in record.message
        for record in caplog.records
    )


def test_inspection_unavailable_warning_logs_only_once_per_session(
    clipboard_monitor_mac, monkeypatch, caplog
):
    monkeypatch.setattr(clipboard_monitor_mac, "_inspection_unavailable_warned", False)
    monkeypatch.delitem(sys.modules, "AppKit", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "AppKit":
            raise ImportError("no module named AppKit")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("WARNING"):
        clipboard_monitor_mac._current_pasteboard_types()
        clipboard_monitor_mac._current_pasteboard_types()
        clipboard_monitor_mac._current_pasteboard_types()

    warnings = [
        r
        for r in caplog.records
        if "clipboard type inspection unavailable -- failing closed" in r.message
    ]
    assert len(warnings) == 1


# --- _runner() wiring tests (TOCTOU before+after, F1; files_processed, F3) ---


def _run_one_tick(clipboard_monitor_mac, monkeypatch, **runner_kwargs):
    """Drive _runner() for exactly one poll iteration: stub time.sleep to
    clear the module's _run flag so the while loop exits cleanly after
    completing the current iteration's body, then call _runner() inline
    (not in a thread -- this is a synchronous, single-iteration test)."""

    def _one_shot_sleep(_seconds):
        clipboard_monitor_mac._run = False

    monkeypatch.setattr(clipboard_monitor_mac.time, "sleep", _one_shot_sleep)
    monkeypatch.setattr(clipboard_monitor_mac, "_first_run", False)
    monkeypatch.setattr(clipboard_monitor_mac, "_run", True)
    monkeypatch.setattr(clipboard_monitor_mac, "_block_image_once", False)
    clipboard_monitor_mac._runner(**runner_kwargs)


def _install_fake_content(clipboard_monitor_mac, monkeypatch, text=None, files=None):
    """Install a fake pasteboard.Pasteboard whose instances answer according
    to their creation order inside _runner(): the first instance created is
    always pb_text (get_contents), the second (only when
    enable_file_monitoring=True and enable_image_monitoring=False, matching
    every test in this file) is pb_files (get_file_urls)."""
    created = []

    class FakePasteboard:
        def __init__(self):
            self.index = len(created)
            created.append(self)

        def get_contents(self, type=None, diff=True):
            return text if self.index == 0 else None

        def get_file_urls(self, diff=True):
            return files if self.index == 1 else None

        def set_contents(self, *args, **kwargs):
            return True

    monkeypatch.setattr(clipboard_monitor_mac.pasteboard, "Pasteboard", FakePasteboard)


def test_runner_sends_when_before_and_after_checks_are_clean(
    clipboard_monitor_mac, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_mac,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    monkeypatch.setattr(
        clipboard_monitor_mac,
        "_current_pasteboard_types",
        lambda: ["public.utf8-plain-text"],
    )
    _install_fake_content(clipboard_monitor_mac, monkeypatch, text="hello world")

    _run_one_tick(
        clipboard_monitor_mac,
        monkeypatch,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert sent == [("text", "hello world")]


def test_runner_skips_when_only_after_check_is_sensitive(
    clipboard_monitor_mac, monkeypatch
):
    """TOCTOU (F1): a sensitive item appearing between the pre-read check
    and the actual content read must still be caught, via the post-read
    re-check -- even though the pre-read check alone was clean."""
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_mac,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    type_checks = Mock(
        side_effect=[
            ["public.utf8-plain-text"],  # pre-read: clean
            ["org.nspasteboard.ConcealedType"],  # post-read: sensitive
        ]
    )
    monkeypatch.setattr(clipboard_monitor_mac, "_current_pasteboard_types", type_checks)
    _install_fake_content(clipboard_monitor_mac, monkeypatch, text="super-secret-password")

    _run_one_tick(
        clipboard_monitor_mac,
        monkeypatch,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert sent == []
    assert type_checks.call_count == 2


def test_runner_sends_nothing_when_inspection_raises(clipboard_monitor_mac, monkeypatch):
    """Fail-closed (F2) exercised end-to-end through _runner(): AppKit
    unavailable -> _current_pasteboard_types() returns None (its own
    try/except never re-raises) -> should_skip_pasteboard_types(None) is
    True -> no callback."""
    monkeypatch.setattr(clipboard_monitor_mac, "_inspection_unavailable_warned", False)
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_mac,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    monkeypatch.delitem(sys.modules, "AppKit", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "AppKit":
            raise ImportError("no module named AppKit")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    _install_fake_content(clipboard_monitor_mac, monkeypatch, text="hello world")

    _run_one_tick(
        clipboard_monitor_mac,
        monkeypatch,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert sent == []


def test_runner_skipped_files_branch_sets_files_processed(
    clipboard_monitor_mac, monkeypatch
):
    """F3: a skipped (sensitive) files item must still set files_processed,
    so a normal text representation of the same clipboard change is not
    sent this tick via the files/text mutual-exclusion invariant. Asserted
    by call-count on the type-check mock: if files_processed were NOT set
    in the skip branch, the text branch would run too and call
    _current_pasteboard_types() a 3rd time."""
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_mac,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    type_checks = Mock(
        side_effect=[
            ["public.utf8-plain-text"],  # pre-read (shared): clean
            ["org.nspasteboard.ConcealedType"],  # files post-read: sensitive
            # No further values: if the text branch ran too, the 3rd call
            # below would raise StopIteration instead of returning cleanly.
        ]
    )
    monkeypatch.setattr(clipboard_monitor_mac, "_current_pasteboard_types", type_checks)
    _install_fake_content(
        clipboard_monitor_mac,
        monkeypatch,
        text="some other text on the same pasteboard",
        files=("file:///Users/alice/secrets.kdbx",),
    )

    _run_one_tick(
        clipboard_monitor_mac,
        monkeypatch,
        enable_image_monitoring=False,
        enable_file_monitoring=True,
    )

    assert sent == []
    assert type_checks.call_count == 2
