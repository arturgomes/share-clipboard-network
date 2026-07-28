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
| Password-manager copy (KeePassXC/Bitwarden/1Password) still shows up on the other machine | See "Concealed/transient skip isn't visibly confirmable at default log level" below — the log line will not appear even when the skip *is* working | Verify **behaviorally**, not by log-grepping alone: confirm the secret does *not* arrive on the other machine (see `docs/UAT-checklist.md` AC5) |

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
logging.warning(f"Unable to inspect pasteboard types, assuming non-concealed: {e}")
```

(macOS side; Linux has an equivalent
`"Unable to inspect GTK clipboard targets, assuming non-sensitive"`.) If you
see **this** line, it means type/target inspection itself failed and the
client fell back to *sending* the content — i.e. the opposite of a skip.
Its absence is a good sign, but not proof of a skip on its own.

**Bottom line:** verify AC5 behaviorally (does the secret arrive on the
other machine or not — see `docs/UAT-checklist.md` AC5), not by grepping
for the `DEBUG` confirmation line. If you want the confirmation line for
your own debugging, temporarily change `LOG_LEVEL` to `logging.DEBUG` in
`vendor/ClipCascade_Desktop/src/core/constants.py` and restart the client
(this is a vendor/-territory source edit, not something these docs can do
for you).

## Setup-time blocker: `requirements.txt` not found

`macos/run-client.sh` and `linux/setup.sh` both look for a file named
`requirements.txt` directly under `vendor/ClipCascade_Desktop/`. This
vendored snapshot (tag `3.2.0`, see `vendor/VERSION`) does not ship a file
by that name or in that location — only per-platform files one directory
deeper, under `vendor/ClipCascade_Desktop/src/`:
`requirements_mac.txt`, `requirements_linux.txt`,
`requirements_linux_cli.txt`, `requirements_linux_gui.txt`,
`requirements_win.txt`.

If either script exits with `ERROR: expected requirements file not found`,
copy the right one into the path the script expects, then re-run it:

```bash
# macOS:
cp vendor/ClipCascade_Desktop/src/requirements_mac.txt \
   vendor/ClipCascade_Desktop/requirements.txt
# Ubuntu (GUI/tray mode, matching linux/clipcascade-client.service's --gui true):
cp vendor/ClipCascade_Desktop/src/requirements_linux_gui.txt \
   vendor/ClipCascade_Desktop/requirements.txt
```

This is reported here for visibility (this doc is in `docs/**`); fixing
`macos/run-client.sh` / `linux/setup.sh` themselves is out of this
specialist's territory.
