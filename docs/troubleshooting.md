# Troubleshooting

Symptom-first. Each row links to the deeper explanation below the table
when there's more than a one-liner's worth of context. All file/path
references are real paths in this repo (`vendor/ClipCascade_Desktop/src/...`,
`linux/...`, `macos/...`) — nothing here is invented.

| Symptom | Likely cause | Fix |
|---|---|---|
| Client stuck reconnecting after the server container restarted; sync never comes back on its own | Upstream ClipCascade bug: the reconnect loop doesn't exit, so `systemd`'s `Restart=on-failure` never fires (process never actually fails/exits) | Ubuntu: `systemctl --user restart clipcascade-client.service`. macOS: `launchctl kickstart -k gui/$(id -u)/com.clipcascade.client` |
| No tray/AppIndicator icon on Ubuntu GNOME | AppIndicator GNOME Shell extension not installed/enabled, or client not started with `--gui true` | `sudo apt install gnome-shell-extension-appindicator`, then `gnome-extensions enable ubuntu-appindicators@ubuntu.com` (or via the "Extensions" app), log out/in once. Confirm the systemd unit's `ExecStart` includes `--gui true` (see `linux/clipcascade-client.service`) |
| Login succeeds but then: *"Login successful but websocket connection failed"* | `server_url` / `websocket_url` in the client's `DATA` file points at `localhost` instead of the server's LAN IP | Edit `DATA` (`~/Library/Application Support/ClipCascade/DATA` on macOS, `vendor/ClipCascade_Desktop/src/DATA` on Ubuntu) so both URLs use the Ubuntu box's real LAN IP. This is a known upstream bug: [Sathvik-Rao/ClipCascade#129](https://github.com/Sathvik-Rao/ClipCascade/issues/129) |
| macOS "Quit" from the menu bar hangs / doesn't exit | Known upstream shutdown issue on macOS | Force-kill via Activity Monitor (search "ClipCascade" or "python3"), or from a terminal: `pkill -f ClipCascade` (matches the venv-run `main.py` process by its working path) |
| Copy-to-paste latency feels >2s | Client fell back to the slower polling loop instead of the event-driven `wl-paste --watch` path, or `--polling` wasn't set | See "Latency / which clipboard loop is running" below |
| Pasted text is garbled / not the original text at all (not a connection error — the paste "succeeds" but content is wrong) | `salt` and/or `hash_rounds` differ between `macos/DATA.template`-derived `DATA` and `linux/DATA.template`-derived `DATA` — each client derives its own E2E key locally, so a mismatch means neither can decrypt what the other encrypted | Open both `DATA` files side by side; make `salt` and `hash_rounds` byte-for-byte identical on both machines, then restart both clients |
| Password-manager copy still shows up on the other machine | **Most likely: the source app never marked the copy.** Detection relies on the source setting a marker (`org.nspasteboard.ConcealedType` on macOS / `x-kde-passwordManagerHint` on Linux). Field-verified NOT marking: **Apple's Passwords app** (plain `public.utf8-plain-text` only), **all browser-extension copies**, and **iPhone→Mac Universal Clipboard hops**. Marking correctly: KeePassXC, 1Password 8 native app | Copy secrets from a marker-setting native app, or pause sync from the tray before handling secrets. To check what your app puts on the macOS pasteboard: `.venv-mac/bin/python -c "from AppKit import NSPasteboard; print([str(t) for t in NSPasteboard.generalPasteboard().types() or []])"` right after copying — if there's no `org.nspasteboard.*` type, the app is undetectable. If the marker IS present and it still syncs, see "Concealed/transient skip isn't visibly confirmable at default log level" below and verify behaviorally per `docs/UAT-checklist.md` AC5 |
| **Nothing** syncs at all any more — not just password-manager copies, everything (plain text, files, images) stops arriving on the other machine | Clipboard type/target inspection itself is failing (AppKit/pyobjc broken on macOS, or `xclip`/`wl-paste`/GTK broken on Linux) — see "Fail-closed: what it means when inspection breaks" below | Check the log for `clipboard type inspection unavailable -- failing closed, items will NOT be synced until this is resolved`. Fix whatever broke inspection (reinstall `pyobjc-framework-Cocoa`/`gi`, confirm `xclip`/`wl-clipboard` are installed and on `PATH`), then restart the client |

## Latency / which clipboard loop is running

`vendor/ClipCascade_Desktop/src/clipboard/clipboard_monitor_linux.py` picks
one of three loops on Linux, in this priority order:

1. **GTK owner-change** (`Gtk.Clipboard.connect("owner-change", ...)`) —
   only attempted when the display is detected as X11.
2. **`wl-paste --watch`** (`_monitor_wl_watch`) — event-driven, used when
   not X11 (i.e. GNOME/Wayland, which is this project's target). No fixed
   poll interval; latency is driven by the event, not a sleep.
3. **`xclip`/`wl-paste` polling** (`_monitor_x_wl_clipboard`) — fallback
   used only if `wl-paste --watch` isn't available or exits immediately.
   This loop sleeps for a fixed interval between reads (default 3s on
   upstream; the `--polling 1` flag in `linux/clipcascade-client.service`'s
   `ExecStart` overrides this to 1s specifically to stay under this
   project's ~2s latency budget).

If latency is high, check `journalctl --user -u clipcascade-client.service`
for one of:

- `Using wl-paste --watch for clipboard monitoring (no focus stealing)` —
  event-driven, latency should be well under 2s.
- `Falling back to wl-paste polling mode for clipboard monitoring` — polling
  path; confirm `--polling 1` is actually in effect (re-run
  `linux/install.sh` if the unit file predates that flag, or check
  `systemctl --user cat clipcascade-client.service` for the live
  `ExecStart` line).
- `wl-paste not found, cannot use --watch mode` — `wl-clipboard` isn't
  installed; `sudo apt install wl-clipboard` (also done by
  `linux/setup.sh`).

Measuring latency for the UAT checklist: use a stopwatch (phone timer) for
a manual copy→paste pass, or script it — e.g. on the sending machine, copy
a string that includes `date +%s.%N`'s output, then on the receiving
machine, paste and diff the embedded timestamp against `date +%s.%N` at
paste time. See `docs/UAT-checklist.md` AC2/AC3 for the exact steps.

## Concealed/transient skip isn't visibly confirmable at default log level

Both patches (`patches/0001-macos-concealed-skip.patch`,
`patches/0002-linux-pm-hint-skip.patch`) log a skip with:

```python
logging.debug("skipped concealed/transient clipboard item")
```

But `vendor/ClipCascade_Desktop/src/core/constants.py` sets
`LOG_LEVEL = logging.INFO`, and `core/application.py`'s
`setup_logging()` configures `logging.basicConfig(level=LOG_LEVEL, ...)`
globally, with no runtime override anywhere in the vendored source. Python's
`logging` module drops `DEBUG` records below the configured level — so
**this confirmation line will never appear in
`clipcascade_log.log` / `journalctl` under the default configuration**,
whether or not the skip is actually happening. Do not treat its absence as
a failure signal.

The only related line that *does* surface at the default `INFO` level is
the failure-path warning:

```python
logging.warning(
    "clipboard type inspection unavailable -- failing closed, "
    f"items will NOT be synced until this is resolved ({e})"
)
```

(Same message text on both platforms; logged from
`_current_pasteboard_types()` on macOS and from `_gtk_clipboard_targets()`/
`_list_mime_targets()` on Linux.) If you see **this** line, it means type/
target inspection itself failed — and, per AC5, the client **fails
CLOSED**: it stops sending *everything* (not just password-manager copies)
until inspection is working again. This is the opposite of the old
fail-open behavior; an unclassifiable clipboard item is never treated as
safe-to-send. See "Fail-closed: what it means when inspection breaks"
below for the full explanation and the symptom this produces.

**Bottom line:** verify AC5 behaviorally (does the secret arrive on the
other machine or not — see `docs/UAT-checklist.md` AC5), not by grepping
for the `DEBUG` confirmation line. If you want the confirmation line for
your own debugging, temporarily change `LOG_LEVEL` to `logging.DEBUG` in
`vendor/ClipCascade_Desktop/src/core/constants.py` and restart the client
(this is a vendor/-territory source edit, not something these docs can do
for you).

## Fail-closed: what it means when inspection breaks

AC5 requires that secret clipboard content is **never** synced, even when
the patch can't tell what's on the clipboard. So the concealed/transient
(macOS) and password-manager-hint (Linux) checks fail **CLOSED**, not open:

- `should_skip_pasteboard_types(None)` and `should_skip_mime_targets(None)`
  both return `True` (skip) — `None` means "could not inspect", and an
  unclassifiable item is always treated as sensitive, never as safe.
- An empty-but-successfully-read list (`[]`) still returns `False` (send) —
  that's the normal case for ordinary clipboard content once inspection
  itself is working.
- Every send additionally re-checks types/targets **after** reading the
  actual content (not just before), so an item copied in the split-second
  between the pre-check and the read is still caught by the post-check.

**Symptom of a persistent inspection failure:** the client silently stops
syncing *all* clipboard content — text, files, and images alike — not just
secrets. The only signal is the one-time-per-process warning above
(`clipboard type inspection unavailable -- failing closed, items will NOT
be synced until this is resolved`); it is logged once and then throttled so
it doesn't spam `clipcascade_log.log`/`journalctl` on every poll tick. If
sync has stopped entirely and you find this line, the fix is to repair
whatever inspection depends on (pyobjc/AppKit on macOS; `gi`/GTK or
`xclip`/`wl-paste` on Linux — see `docs/setup-ubuntu.md` B1 for the
packages that provide these) and restart the client. This is a deliberate
trade-off: sync going dead is safe and loud; sync silently leaking a
password is not acceptable under any circumstance.

## Setup fails with `ERROR: expected requirements file not found`

The vendored snapshot (tag `3.2.0`, see `vendor/VERSION`) ships per-platform
requirements files under `vendor/ClipCascade_Desktop/src/`
(`requirements_mac.txt`, `requirements_linux.txt`, etc.), not a single
`requirements.txt`. `macos/run-client.sh` uses `src/requirements_mac.txt` and
`linux/setup.sh` uses `src/requirements_linux.txt`. If you see this error,
your checkout predates that fix or the vendored tree is incomplete — run
`git pull` and confirm the file exists at the path printed in the error.
