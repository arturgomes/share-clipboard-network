"""Integration-ish tests for clipboard_monitor_linux's clipboard-monitoring
wiring (GTK owner-change, xclip/wl-paste polling, wl-paste --watch), using
module-boundary mocks -- no gi/PyGObject/GTK needs to be installed to run
this file (clipboard_monitor_linux.py only imports `gi` lazily inside
`_runner`/`stop`, never at module scope), so this runs on macOS with no
GTK installed.

Covers finding F1 (before+after TOCTOU re-check must gate every send) and
finding F2 (a target-listing/inspection failure must fail CLOSED -- no
send) across all three code paths.
"""

import importlib

import pytest


@pytest.fixture
def clipboard_monitor_linux():
    return importlib.import_module("clipboard.clipboard_monitor_linux")


class FakeAtom:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class FakeGtkClipboard:
    """Stands in for Gtk.Clipboard: exposes wait_for_targets/wait_for_text/
    wait_for_uris/wait_for_image with the same call signatures used by
    clipboard_monitor_linux."""

    def __init__(self, targets=(), text=None, uris=None, image=None):
        self._targets = list(targets)
        self._text = text
        self._uris = uris
        self._image = image

    def wait_for_targets(self):
        return True, [FakeAtom(t) for t in self._targets]

    def wait_for_text(self):
        return self._text

    def wait_for_uris(self):
        return self._uris

    def wait_for_image(self):
        return self._image


def test_gtk_clipboard_targets_returns_target_names(clipboard_monitor_linux):
    clipboard = FakeGtkClipboard(targets=["UTF8_STRING", "x-kde-passwordManagerHint"])
    result = clipboard_monitor_linux._gtk_clipboard_targets(clipboard)
    assert result == ["UTF8_STRING", "x-kde-passwordManagerHint"]


def test_gtk_clipboard_targets_empty_when_no_targets(clipboard_monitor_linux):
    class NoTargetsClipboard:
        def wait_for_targets(self):
            return False, None

    assert clipboard_monitor_linux._gtk_clipboard_targets(NoTargetsClipboard()) == []


def test_gtk_clipboard_targets_degrades_and_fails_closed_on_failure(
    clipboard_monitor_linux, monkeypatch, caplog
):
    monkeypatch.setattr(clipboard_monitor_linux, "_inspection_unavailable_warned", False)

    class BoomClipboard:
        def wait_for_targets(self):
            raise RuntimeError("boom")

    with caplog.at_level("WARNING"):
        result = clipboard_monitor_linux._gtk_clipboard_targets(BoomClipboard())

    # None means "could not inspect" to the caller, which must fail CLOSED
    # (AC5, finding F2) -- never degrade to sending.
    assert result is None
    assert clipboard_monitor_linux.should_skip_mime_targets(result) is True
    assert any(
        "clipboard type inspection unavailable -- failing closed" in record.message
        for record in caplog.records
    )


def test_on_clipboard_changed_skips_pm_hinted_text(clipboard_monitor_linux, monkeypatch):
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    clipboard = FakeGtkClipboard(
        targets=["UTF8_STRING", "x-kde-passwordManagerHint"],
        text="super-secret-password",
    )

    clipboard_monitor_linux._on_clipboard_changed(
        clipboard, enable_image_monitoring=False, enable_file_monitoring=False
    )

    assert sent == []


def test_on_clipboard_changed_sends_normal_text(clipboard_monitor_linux, monkeypatch):
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    clipboard = FakeGtkClipboard(targets=["UTF8_STRING"], text="hello world")

    clipboard_monitor_linux._on_clipboard_changed(
        clipboard, enable_image_monitoring=False, enable_file_monitoring=False
    )

    assert sent == [("text", "hello world")]


def test_on_clipboard_changed_toctou_after_check_catches_race(
    clipboard_monitor_linux, monkeypatch
):
    """F1/TOCTOU: the pre-read target check is clean, but a hint appears by
    the time the post-read check runs (simulating a sensitive item copied
    in the gap between the two) -- the item must still be skipped."""
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )

    class RacyClipboard(FakeGtkClipboard):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._targets_calls = 0

        def wait_for_targets(self):
            self._targets_calls += 1
            if self._targets_calls == 1:
                return True, [FakeAtom("UTF8_STRING")]
            return True, [FakeAtom("UTF8_STRING"), FakeAtom("x-kde-passwordManagerHint")]

    clipboard = RacyClipboard(text="super-secret-password")

    clipboard_monitor_linux._on_clipboard_changed(
        clipboard, enable_image_monitoring=False, enable_file_monitoring=False
    )

    assert sent == []
    assert clipboard._targets_calls == 2


def test_on_clipboard_changed_skips_pm_hinted_files(clipboard_monitor_linux, monkeypatch):
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    clipboard = FakeGtkClipboard(
        targets=["text/uri-list", "x-kde-passwordManagerHint"],
        uris=["file:///home/user/secrets.kdbx"],
    )

    clipboard_monitor_linux._on_clipboard_changed(
        clipboard, enable_image_monitoring=False, enable_file_monitoring=True
    )

    assert sent == []


# --- _monitor_x_wl_clipboard() wiring (mocked command executor) ---


def _run_one_tick_x_wl(clipboard_monitor_linux, monkeypatch, x_mode, **kwargs):
    """Drive _monitor_x_wl_clipboard() for exactly one poll iteration: stub
    time.sleep to clear _run_poll so the while loop exits cleanly after
    completing the current iteration's body (including the failure/continue
    path, which also sleeps before looping)."""

    def _one_shot_sleep(_seconds):
        clipboard_monitor_linux._run_poll.clear()

    monkeypatch.setattr(clipboard_monitor_linux.time, "sleep", _one_shot_sleep)
    monkeypatch.setattr(clipboard_monitor_linux, "_block_image_once", False)
    clipboard_monitor_linux._run_poll.set()
    clipboard_monitor_linux._monitor_x_wl_clipboard(x_mode=x_mode, **kwargs)


def _fake_execute_command(targets_responses, content_response=None):
    """Fake for clipboard_monitor_linux.execute_command: TARGETS/-l calls
    are answered from `targets_responses` in order (so pre- and post-read
    checks can return different values), any other call (a content read)
    returns `content_response`."""
    targets_responses = list(targets_responses)
    targets_arg_sets = (
        ("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"),
        ("wl-paste", "-l"),
    )

    def fake(*args):
        if args in targets_arg_sets:
            return targets_responses.pop(0)
        return content_response

    return fake


def test_monitor_x_wl_clipboard_sends_when_before_and_after_checks_are_clean(
    clipboard_monitor_linux, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "execute_command",
        _fake_execute_command(
            targets_responses=[
                (True, b"UTF8_STRING\n"),  # pre-read: clean
                (True, b"UTF8_STRING\n"),  # post-read: still clean
            ],
            content_response=(True, b"hello world"),
        ),
    )

    _run_one_tick_x_wl(
        clipboard_monitor_linux,
        monkeypatch,
        x_mode=False,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert sent == [("text", "hello world")]


def test_monitor_x_wl_clipboard_toctou_after_check_catches_race(
    clipboard_monitor_linux, monkeypatch
):
    """F1/TOCTOU: pre-read target listing is clean; by the time the
    post-read re-check runs, the password-manager hint has appeared
    (simulating a sensitive item copied in the gap) -- must still skip."""
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "execute_command",
        _fake_execute_command(
            targets_responses=[
                (True, b"UTF8_STRING\n"),  # pre-read: clean
                (True, b"UTF8_STRING\nx-kde-passwordManagerHint\n"),  # post-read: sensitive
            ],
            content_response=(True, b"super-secret-password"),
        ),
    )

    _run_one_tick_x_wl(
        clipboard_monitor_linux,
        monkeypatch,
        x_mode=False,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert sent == []


def test_monitor_x_wl_clipboard_target_listing_failure_sends_nothing(
    clipboard_monitor_linux, monkeypatch
):
    """F2: a target-listing failure must fail CLOSED -- no send at all,
    not even an attempt to read/classify content this tick."""
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )

    def always_fails(*args):
        return (False, "boom: wl-paste not found")

    monkeypatch.setattr(clipboard_monitor_linux, "execute_command", always_fails)

    _run_one_tick_x_wl(
        clipboard_monitor_linux,
        monkeypatch,
        x_mode=False,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert sent == []


# --- _monitor_wl_watch() wiring (mocked subprocess + command executor) ---


class _FakeWlPasteWatchProc:
    """Stands in for the `wl-paste --watch echo` subprocess.Popen handle:
    each queued line represents one clipboard-change event; an empty
    readline() return (EOF) ends the while loop naturally, no time.sleep
    stub needed for this one."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.stdout = self
        self.stderr = self

    def poll(self):
        return None  # still running

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    def read(self):
        return b""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _run_wl_watch(clipboard_monitor_linux, monkeypatch, num_events=1, **kwargs):
    fake_proc = _FakeWlPasteWatchProc(lines=[b"event\n"] * num_events)
    monkeypatch.setattr(
        clipboard_monitor_linux.subprocess, "Popen", lambda *a, **k: fake_proc
    )
    monkeypatch.setattr(clipboard_monitor_linux.time, "sleep", lambda _s: None)
    monkeypatch.setattr(clipboard_monitor_linux, "_block_image_once", False)
    clipboard_monitor_linux._run_poll.set()
    return clipboard_monitor_linux._monitor_wl_watch(**kwargs)


def test_monitor_wl_watch_sends_when_before_and_after_checks_are_clean(
    clipboard_monitor_linux, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "execute_command",
        _fake_execute_command(
            targets_responses=[
                (True, b"UTF8_STRING\n"),  # pre-read: clean
                (True, b"UTF8_STRING\n"),  # post-read: still clean
            ],
            content_response=(True, b"hello world"),
        ),
    )

    result = _run_wl_watch(
        clipboard_monitor_linux,
        monkeypatch,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert result is True
    assert sent == [("text", "hello world")]


def test_monitor_wl_watch_target_listing_failure_sends_nothing(
    clipboard_monitor_linux, monkeypatch
):
    """F2: a target-listing failure must fail CLOSED -- no send."""
    sent = []
    monkeypatch.setattr(
        clipboard_monitor_linux,
        "_callback_update",
        lambda kind, payload: sent.append((kind, payload)),
    )

    def always_fails(*args):
        return (False, "boom: wl-paste not found")

    monkeypatch.setattr(clipboard_monitor_linux, "execute_command", always_fails)

    result = _run_wl_watch(
        clipboard_monitor_linux,
        monkeypatch,
        enable_image_monitoring=False,
        enable_file_monitoring=False,
    )

    assert result is True
    assert sent == []
