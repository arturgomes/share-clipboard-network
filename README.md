# share-clipboard-network

Universal Clipboard, but for a MacBook and an Ubuntu desktop.

## What / Why

macOS ships Universal Clipboard, but only between Apple devices — there is no
supported way to bridge it to a non-Apple machine. Apple's Universal Clipboard /
Continuity protocol is undocumented, private, and tied to Apple ID + Bluetooth/
Wi-Fi device pairing; reverse-engineering and impersonating it was evaluated and
rejected as infeasible for a home project (no public spec, no stable API,
high likelihood of breaking silently on OS updates).

Instead, this repo wires up a self-hosted clipboard sync tool
([ClipCascade](https://github.com/Sathvik-Rao/ClipCascade), pinned at `3.2.0`)
to reproduce the same outcome: copy text on the Mac, paste it on the Ubuntu
box, and vice versa, over the home LAN — with the clipboard payload
end-to-end encrypted so the server itself never sees plaintext.

Scope for v1 is **text only** (image/file sharing disabled on both clients —
see `macos/DATA.template` / `linux/DATA.template`).

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
  login password (+ optional salt, 664,937 hash rounds by default) — salt and
  rounds must match on both clients or they cannot decrypt each other's
  clipboard updates.
- Transport is plain HTTP/WS on the LAN (no native TLS in ClipCascade). This
  is accepted as a home-LAN threat model; do not expose port 8080 beyond the
  LAN without adding a reverse proxy/VPN in front of it.
- Clients must talk to the server's **LAN IP**, never `localhost` — see
  `server/README.md` for the known upstream bug this works around.
- **Secrets caveat (read this):** the clients are patched to skip clipboard
  items *marked* sensitive by their source app (`org.nspasteboard.ConcealedType`
  on macOS, `x-kde-passwordManagerHint` on Linux — e.g. KeePassXC, 1Password 8
  native app). Apps that do **not** mark — verified: **Apple's Passwords app**,
  all **browser-extension** password copies, and **iPhone→Mac Universal
  Clipboard** hops — produce plain text that syncs like any other copy.
  There is no reliable content-based detection for unmarked secrets. When in
  doubt: pause sync from the tray first. Details: `docs/troubleshooting.md`.

## Quickstart

1. **Server** (on the Ubuntu desktop): follow `server/README.md` to bring up
   the ClipCascade container via Docker Compose.
2. **macOS client**: follow `docs/setup-macos.md`.
3. **Ubuntu client**: follow `docs/setup-ubuntu.md`.

(`docs/setup-macos.md` and `docs/setup-ubuntu.md` are authored by a separate
lane in this repo and are not part of this specialist's territory.)

## Repo layout

- `server/` — Docker Compose file + server-side setup notes (this lane).
- `macos/` — macOS client runtime scripts, LaunchAgent, config template (this lane).
- `linux/` — Ubuntu client runtime scripts, systemd user unit, config template (this lane).
- `vendor/` — vendored + patched ClipCascade desktop client source (owned by another lane).
- `docs/` — end-user setup walkthroughs (owned by another lane).
