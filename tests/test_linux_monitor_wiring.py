"""Integration-ish tests for clipboard_monitor_linux's GTK owner-change
wiring, using a fake Gtk.Clipboard-shaped object -- no gi/PyGObject/GTK
needs to be installed to run this file (clipboard_monitor_linux.py only
imports `gi` lazily inside `_runner`/`stop`, never at module scope), so
this runs on macOS with no GTK installed.
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


def test_gtk_clipboard_targets_degrades_on_failure(clipboard_monitor_linux, caplog):
    class BoomClipboard:
        def wait_for_targets(self):
            raise RuntimeError("boom")

    with caplog.at_level("WARNING"):
        result = clipboard_monitor_linux._gtk_clipboard_targets(BoomClipboard())

    assert result is None
    assert any(
        "Unable to inspect GTK clipboard targets" in record.message
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
