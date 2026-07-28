"""Integration-ish tests for clipboard_monitor_mac's pasteboard-type
inspection wiring, using module-boundary mocks -- no real `pasteboard`
package or AppKit/pyobjc is installed (or needed) to run this file.

`clipboard_monitor_mac.py` does `import pasteboard` unconditionally at
module scope, so a fake `pasteboard` module is installed in sys.modules
before the module is first imported. `from AppKit import NSPasteboard`
happens lazily inside `_current_pasteboard_types()`, so AppKit is only
faked per-test via `builtins.__import__`/sys.modules, exactly the
module-boundary the function was designed around.
"""

import builtins
import sys
import types

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

    assert result == []
    assert clipboard_monitor_mac.should_skip_pasteboard_types(result) is False


def test_current_pasteboard_types_degrades_when_appkit_unavailable(
    clipboard_monitor_mac, monkeypatch, caplog
):
    monkeypatch.delitem(sys.modules, "AppKit", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "AppKit":
            raise ImportError("no module named AppKit")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("WARNING"):
        result = clipboard_monitor_mac._current_pasteboard_types()

    # None means "unknown" to the caller, which must degrade to sending
    # (current upstream behavior) rather than crash the monitor.
    assert result is None
    assert clipboard_monitor_mac.should_skip_pasteboard_types(result) is False
    assert any(
        "Unable to inspect pasteboard types" in record.message
        for record in caplog.records
    )
