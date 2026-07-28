# share-clipboard-network

Universal Clipboard, but for a MacBook **and** an Ubuntu desktop — copy on one,
paste on the other, both directions, in under ~2 seconds over the home LAN.

**Bonus that fell out for free:** copy on an **iPhone** → paste on **Linux**.
Apple's own Universal Clipboard carries the item iPhone → Mac, and this setup
relays it Mac → Ubuntu. Two hops, fully automatic:

```
iPhone ──(Apple Universal Clipboard)──► MacBook ──(this repo)──► Ubuntu
```

## What / Why

macOS ships Universal Clipboard, but only between Apple devices — there is no
supported way to bridge it to a non-Apple machine. Apple's Continuity protocol
is undocumented and private (BLE + AWDL peer-to-peer Wi-Fi, Apple-ID-derived
crypto); joining it from Linux was researched and rejected: no public spec, no
working prior art anywhere, and reverse-engineered Apple-ID auth carries a real
account-lockout risk.

Instead, this repo wires up a self-hosted clipboard sync tool
([ClipCascade](https://github.com/Sathvik-Rao/ClipCascade), clients vendored at
release `3.2.0`) to reproduce the same outcome — with the clipboard payload
end-to-end encrypted, so the relay server never sees plaintext.

Scope is **text only** (image/file sharing disabled on both clients).

**Status: working.** Verified in the field on macOS (Apple Silicon) + Ubuntu
(GNOME/Wayland): both directions, iPhone relay, server auto-start after reboot.

## Architecture

```
 ┌─────────────────────────┐                              ┌──────────────────────────┐
 │   MacBook (macOS)        │                              │  Ubuntu Desktop           │
 │                           │                              │                           │
 │  ClipCascade client       │                              │  ClipCascade client        │
 │  (vendored+patched Python)│                              │  (vendored+patched Python) │
 │  launchd LaunchAgent       │                              │  systemd --user unit       │
 │                           │                              │                           │
 │       clipboard  ◄────────┼─── E2E-encrypted payload ────┼────►  clipboard            │
 └───────────┬───────────────┘        over ws://            └───────────┬───────────────┘
             │                                                          │
             │            HTTP + WebSocket, LAN only, no TLS            │
             └──────────────────────────►  ┌─────────────────────┐ ◄───┘
                                            │  ClipCascade server  │
                                            │  Docker container     │
                                            │  (runs ON the Ubuntu  │
                                            │   desktop, port 8080)  │
                                            │  H2 file DB (cc_users)│
                                            └─────────────────────┘
```

- The server is a relay only: it stores/forwards ciphertext, it does not hold
  the encryption key. Each client derives the key locally from the shared
  login password (+ salt, 664,937 hash rounds by default) — `salt` and
  `hash_rounds` must be identical in both clients' `DATA` files or they cannot
  decrypt each other's clipboard updates.
- Transport is plain HTTP/WS on the LAN (no native TLS in ClipCascade). Fine
  for a home-LAN threat model; do **not** expose port 8080 beyond the LAN
  without a reverse proxy/VPN in front.
- Clients must talk to the server's **LAN IP**, never `localhost` (upstream
  bug [#129](https://github.com/Sathvik-Rao/ClipCascade/issues/129): login
  succeeds but the websocket fails). Give the server a DHCP reservation.

## The secrets caveat (read this)

The vendored clients are **patched** (see `patches/`) to skip clipboard items
*marked* sensitive by their source app (`org.nspasteboard.ConcealedType` on
macOS, `x-kde-passwordManagerHint` on Linux), with TOCTOU-safe before/after
checks and fail-closed behavior when inspection breaks.

That protection only works when the source app cooperates. Field-verified:

| Source of the password copy | Marked? | Synced to the other machine? |
|---|---|---|
| KeePassXC (native app) | yes | **blocked** ✅ |
| 1Password 8 (native app) | yes | **blocked** ✅ |
| **Apple's Passwords app** | **no** — plain `public.utf8-plain-text` only | **leaks** ⚠️ |
| Any **browser-extension** copy | no | **leaks** ⚠️ |
| iPhone → Mac Universal Clipboard hop | no (markers don't survive Continuity) | **leaks** ⚠️ |

There is no reliable content-based detection for unmarked secrets (entropy
heuristics false-positive on URLs/tokens you *want* synced). When handling
secrets from an unmarked source: **pause sync from the tray icon first**, or
copy secrets from a marker-setting app.

## Quickstart

Order matters: server → Ubuntu client → macOS client.

1. **Server** (Ubuntu desktop): `server/README.md` — Docker Compose, first-login
   hardening, create the sync user, fix the LAN IP.
2. **Ubuntu client**: `docs/setup-ubuntu.md` — `./linux/setup.sh`, `DATA`
   config, `./linux/install.sh` (systemd user unit + tray).
3. **macOS client**: `docs/setup-macos.md` — needs
   `brew install python@3.12 python-tk@3.12`, then `DATA` config and
   `./macos/install.sh` (LaunchAgent).
4. Verify with `docs/UAT-checklist.md` (one section per acceptance criterion).

Run the scripts as `./script.sh` — **not** `sh script.sh`, **not** `sudo` —
and on the right machine (they refuse to run on the wrong OS).

## Field notes — every problem we actually hit, and the fix

All hit during real first-time setup (2026-07-28); deeper table in
`docs/troubleshooting.md`.

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied ... /var/run/docker.sock` | `usermod -aG docker` needs a fresh login to take effect | `newgrp docker` in the current shell, or log out/in |
| `manifest for sathvikrao/clipcascade:3.2.0 not found` | Docker Hub image has its **own** version line (`0.x`) — `3.2.0` is only a GitHub release tag | compose pins `sathvikrao/clipcascade:0.7.0` (== `latest` digest at time of check) |
| `set: Illegal option -o pipefail` | script run with `sh` (dash), not bash | run `./script.sh` directly (execute bits are committed) |
| `systemctl: command not found` on the Mac | ran `linux/install.sh` on macOS | scripts now uname-guard and point you to the right directory |
| launchd job dies with `Operation not permitted` (exit 126) | macOS **TCC**: background LaunchAgents can't read `~/Documents` without Full Disk Access; the grant may only apply after logout/login | grant `/bin/bash` Full Disk Access + re-login, or run manually from Terminal (`nohup ./macos/run-client.sh &` — inherits Terminal's permission, but won't survive reboot), or keep the repo outside `~/Documents` |
| `Failed building wheel for pasteboard` / `pyobjus` on macOS | Python 3.13/3.14 — those packages ship no wheels for it and their source builds die on current clang `-Werror` | Python **3.12** required; `macos/run-client.sh` enforces it |
| `ModuleNotFoundError: No module named '_tkinter'` | Homebrew packages Tk separately; the login window needs it | `brew install python-tk@3.12` |
| pip warning about `yt-dlp` / `websockets` conflict during `linux/setup.sh` | venv uses `--system-site-packages`; pip notices the system `yt-dlp` | harmless — the venv's own pins win inside the venv, system packages untouched |
| "connection refused" on client login | server container down, `DATA` still has the `SERVER_LAN_IP` placeholder, or ufw rejecting 8080 | `docker compose ps` / fix `DATA` / `sudo ufw allow 8080/tcp` |
| Password from Apple's Passwords app reached Linux | app doesn't mark its copies — see the secrets caveat above | use a marker-setting app or pause sync |
| Client stuck reconnecting after server restart | upstream bug ([PR #161](https://github.com/Sathvik-Rao/ClipCascade/pull/161) unmerged) — process loops without exiting, so systemd's restart never fires | Ubuntu: `systemctl --user restart clipcascade-client` · Mac: `launchctl kickstart -k gui/$(id -u)/com.clipcascade.client` |
| No tray icon on Ubuntu GNOME | AppIndicator extension missing/disabled, or client not started with `--gui true` | `gnome-extensions enable ubuntu-appindicators@ubuntu.com`, log out/in; the systemd unit already passes `--gui true` |

## Repo layout

- `server/` — Docker Compose + server setup/hardening notes.
- `macos/` — client run script, LaunchAgent plist, installer, `DATA.template`.
- `linux/` — client setup/installer scripts, systemd user unit, `DATA.template`.
- `vendor/ClipCascade_Desktop/` — upstream client source at `3.2.0` (GPL-3.0,
  provenance in `vendor/VERSION`) plus our patches applied in-tree.
- `patches/` — the same patches as reviewable diffs against pristine upstream.
- `tests/` — pytest suite for the patch logic (38 tests; run with any modern
  Python: `python3 -m pytest tests/`).
- `docs/` — setup walkthroughs, troubleshooting, UAT checklist.

## License

Vendored ClipCascade code is GPL-3.0 (see `vendor/ClipCascade_Desktop/LICENSE`);
the patches modify it and are distributed under the same terms.
