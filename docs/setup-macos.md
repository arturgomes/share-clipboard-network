# macOS client setup

This is the client-only side (the server + Ubuntu client are covered in
`docs/setup-ubuntu.md`). Do `docs/setup-ubuntu.md` Part A first so you have
a `SERVER_LAN_IP` and a non-admin sync username before configuring this
machine.

Scope reminder: text-only v1 — image/file sharing must be **disabled** on
this client too (`enable_image_sharing` / `enable_file_sharing` in
`macos/DATA.template`), matching the Ubuntu side.

## 1. Prerequisites

- **Python 3.12 specifically**, plus its Tk bindings:
  `brew install python@3.12 python-tk@3.12`. Newer Pythons (3.13/3.14) do
  NOT work — `pasteboard`/`pyobjus` ship no wheels for them and their source
  builds fail under current clang; the login window needs tkinter, which
  Homebrew packages separately. `macos/run-client.sh` checks both and exits
  with instructions if missing.
- No app install needed — the client runs **from the vendored source**
  (`vendor/ClipCascade_Desktop/`) via a venv, which also sidesteps macOS
  Gatekeeper's unsigned-app warning (there's no unsigned `.app` bundle to
  flag; it's just a local Python process). A first run may still prompt for
  a **local network permission** dialog (macOS's per-app LAN access
  prompt) — allow it, or the client cannot reach the server on the LAN.

## 2. Run the client (manual dry run first)

```bash
./macos/run-client.sh
```

`macos/run-client.sh`:

1. Verifies `vendor/ClipCascade_Desktop/` and `src/main.py` exist (fails
   fast with a clear message if the vendoring lane's work hasn't landed).
2. Creates/reuses a venv at `<repo-root>/.venv-mac`.
3. Installs the client's Python deps into it.
4. Runs `python3 vendor/ClipCascade_Desktop/src/main.py`.

The script installs deps from
`vendor/ClipCascade_Desktop/src/requirements_mac.txt`. If it exits with
`ERROR: expected requirements file not found`, see
`docs/troubleshooting.md`.

The first time you run this, expect a **login window** to pop up (see
step 4 below) rather than a silent background start — that's expected for
a first run with no saved session.

## 3. Configure the client (DATA file)

The client reads its config from a JSON file literally named `DATA`. On
macOS, `core/constants.py`'s `get_program_files_directory()` resolves this
to `~/Library/Application Support/ClipCascade/` (the app creates this
directory itself if missing, but you can create it up front):

```bash
mkdir -p ~/"Library/Application Support/ClipCascade"
cp macos/DATA.template ~/"Library/Application Support/ClipCascade/DATA"
```

Edit `~/Library/Application Support/ClipCascade/DATA` and:

1. Delete the `_comment_` key (JSON has no comment syntax; it's
   template-only explanation).
2. Replace `SERVER_LAN_IP` in both `server_url` and `websocket_url` with
   the Ubuntu desktop's LAN IP from `docs/setup-ubuntu.md` A5. **Never**
   use `localhost` — see that doc's A5 and
   [upstream issue #129](https://github.com/Sathvik-Rao/ClipCascade/issues/129).
3. Set `username` to the non-admin sync user created in
   `docs/setup-ubuntu.md` A4.
4. Set `salt` to the **exact same value** you put in `linux/DATA.template`
   on the Ubuntu side, and confirm `hash_rounds` (default `664937`)
   matches too — the E2E key is derived from the login password + salt run
   through `hash_rounds` iterations; a mismatch means each client can send
   but never correctly decrypt the other's clipboard payloads (garbled
   output, not an error — see `docs/troubleshooting.md`).
5. Confirm `enable_image_sharing` and `enable_file_sharing` are both
   `false`, matching the Ubuntu side (AC7 / text-only scope).

`DATA` is gitignored (it holds derived key material and the server's LAN
IP) — do not commit it.

## 4. First run / login

Run `./macos/run-client.sh` again (or for the first time, if you configured
`DATA` before ever running it). A login window appears, pre-filled from
`DATA` with `server_url` and `username`. Enter the sync user's password and
submit.

- Success shows: *"Success! ClipCascade will now run in the task bar/menu
  bar."* — look for the menu-bar icon.
- *"Login successful but websocket connection failed"* means
  `server_url`/`websocket_url` is still pointed at `localhost` or an
  unreachable IP — see `docs/setup-ubuntu.md` A5 and
  `docs/troubleshooting.md`.

On success, the client writes a session cookie + hashed password back into
`DATA`, so future starts (including via the LaunchAgent below) skip the
login window.

Stop this manual dry run (Ctrl-C, or Quit from the menu-bar icon — see the
quit-hang caveat in `docs/troubleshooting.md`) before installing the
LaunchAgent in the next step, so the two don't fight over the same lock
file.

## 5. Install the LaunchAgent (autostart)

```bash
./macos/install.sh
```

This:

1. Substitutes this machine's real absolute repo path and home directory
   into `macos/com.clipcascade.client.plist` (launchd plists can't expand
   `~`/`$HOME`, hence the substitution — see comments in that file).
2. Copies the result to `~/Library/LaunchAgents/com.clipcascade.client.plist`.
3. Runs `launchctl bootstrap gui/$(id -u) …` (after a tolerant `bootout` of
   any prior copy) so it starts now and on every future login
   (`RunAtLoad = true`) — no manual start needed after a reboot (AC4).

`KeepAlive.SuccessfulExit = false` means launchd relaunches the client if it
exits with a non-zero status, but does not treat a clean exit as something
to restart (so a deliberate quit stays quit).

Check it loaded:

```bash
launchctl list | grep com.clipcascade.client
```

## 6. Log locations

Two separate logs exist on macOS:

- **App-internal log** (from the client's own `logging` setup):
  `~/Library/Application Support/ClipCascade/clipcascade_log.log`
  — this is where the client's own `INFO`/`WARNING`/`ERROR` lines land
  (see the log-level caveat in `docs/troubleshooting.md` — the
  concealed-item skip line is logged at `DEBUG`, which this file does not
  capture by default).
- **LaunchAgent stdout/stderr** (from launchd, per
  `com.clipcascade.client.plist`'s `StandardOutPath`/`StandardErrorPath`):
  `~/Library/Logs/clipcascade.log`
  — this captures anything printed directly to stdout/stderr outside the
  `logging` module (e.g. uncaught tracebacks before logging is configured).

```bash
tail -f ~/"Library/Application Support/ClipCascade/clipcascade_log.log"
tail -f ~/Library/Logs/clipcascade.log
```

## 7. Verify

- Confirm the menu-bar icon is present.
- Confirm `launchctl list | grep com.clipcascade.client` shows the label
  with a `0` last-exit-status (a large/negative number usually means it's
  crash-looping — check the two logs above).
- Then follow `docs/UAT-checklist.md` for the actual cross-machine sync
  tests (AC2/AC3), concealed-item test (AC5), offline test (AC6), and
  non-text test (AC7).

## Known issues affecting this setup

See `docs/troubleshooting.md` for: reconnect loops after a server restart,
the localhost/websocket failure, quit hangs, and latency/log-level
gotchas.
