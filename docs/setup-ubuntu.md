# Ubuntu desktop setup (server + client)

This machine runs **both** the ClipCascade server (Docker) and the Ubuntu
ClipCascade client. Do the server section first, then the client section.

Scope reminder: this is a **text-only** sync setup — image/file sharing is
disabled on both clients (see `enable_image_sharing` / `enable_file_sharing`
in `linux/DATA.template`).

## Part A — Server (Docker)

### A1. Install Docker

```bash
sudo apt update
sudo apt install docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out/in (or `newgrp docker`) to pick this up
```

If you need a newer Docker/Compose than Ubuntu ships, use Docker's own
`docker-ce` repo instead: https://docs.docker.com/engine/install/ubuntu/
(see `server/README.md` for both options).

`server/docker-compose.yml` pins `sathvikrao/clipcascade:0.7.0` — note the
Docker Hub image has its own version line (0.x), separate from the GitHub
release tags (3.x) the desktop clients come from. 0.7.0 is multi-arch
(amd64/arm64/ppc64le/s390x), so both common desktop architectures are
covered.

### A2. Bring the server up

```bash
cd server/
docker compose up -d
docker compose ps
docker compose logs -f
```

This starts the container on port `8080` and creates `server/cc_users/`
(gitignored) holding the H2 database.

Ubuntu Desktop ships with `ufw` **inactive** by default, so port `8080` is
reachable on the LAN out of the box. If you've enabled `ufw` on this
machine, open the port explicitly or the other client will fail to reach
the server (AC1):

```bash
sudo ufw allow 8080/tcp
```

`restart: unless-stopped` in
`server/docker-compose.yml` means the container restarts automatically
whenever the Docker daemon restarts — including after a full desktop
reboot, since `systemctl enable --now docker` (step A1) makes `docker.service`
start on boot. This is what satisfies AC1 ("server ... restarts automatically
after desktop reboot") — verify it for real, don't just take this on faith:
reboot the desktop, then re-run `docker compose ps` from `server/` and confirm
the container shows `Up`.

### A3. First-login hardening (do this immediately)

ClipCascade ships with signup disabled and a public default admin account:

- username: `admin`
- password: `admin123`

1. Find this machine's LAN IP: `ip addr show | grep 'inet '` (pick the LAN
   interface, not `127.0.0.1`).
2. Open `http://<this-machine-LAN-IP>:8080` in a browser.
3. Log in as `admin` / `admin123`.
4. **Change the admin password immediately** — `admin123` is public
   knowledge (it's in ClipCascade's own upstream docs) and nothing else
   gates access to this server on the LAN.

### A4. Create the non-admin sync user

The Mac and this Ubuntu desktop should sync as one **regular (non-admin)**
user, not `admin`. Create it from the `/dashboard` admin panel after logging
in.

**Honesty note (unverified step):** the exact click-path for user creation
in the 3.2.0 dashboard is not documented upstream and was not exercised live
as part of authoring this doc — expect a "Users" or "Admin" section under
`/dashboard`. If it isn't obvious, check the ClipCascade GitHub
README/wiki for this version, or open an upstream issue. Whatever username
you create here goes into `username` in both `linux/DATA.template` (below)
and `macos/DATA.template` (see `docs/setup-macos.md`).

### A5. Give the server a stable LAN address

Clients must use this box's **LAN IP**, never `localhost` — pointing a
client at `localhost` fails at the WebSocket layer even though login
succeeds (upstream issue
[Sathvik-Rao/ClipCascade#129](https://github.com/Sathvik-Rao/ClipCascade/issues/129)).
There is no mDNS/`.local` discovery in ClipCascade, so fix the IP one of two
ways:

- Set a **DHCP reservation** for this machine's MAC address in your router
  (recommended, no changes on the Ubuntu box), or
- Assign a **static IP** here via netplan / NetworkManager.

Note the resulting IP as `SERVER_LAN_IP` — it's used in both clients'
`server_url` / `websocket_url` below.

## Part B — Ubuntu ClipCascade client

### B1. Install system packages + create the client venv

```bash
./linux/setup.sh
```

This installs (via `sudo apt install`):

- `python3-venv` — needed to create the client's venv
- `gir1.2-gtk-3.0` — GTK bindings used by the Linux clipboard monitor's X11
  owner-change path
- `gnome-shell-extension-appindicator` — provides the GNOME Shell extension
  that renders AppIndicator/tray icons under Wayland; **without this,
  `--gui true` (below) has no tray to attach to on stock GNOME**
- `wl-clipboard` — gives the client `wl-copy`/`wl-paste` for Wayland
  clipboard access

then creates `.venv-linux/` at the repo root and installs the client's
Python dependencies into it.

**Enable the AppIndicator extension** after installing it (needed once):

```bash
gnome-extensions enable ubuntu-appindicators@ubuntu.com   # or use the "Extensions" app
```

Log out and back in once for the extension to take effect.

`linux/setup.sh` installs deps from
`vendor/ClipCascade_Desktop/src/requirements_linux.txt` (superset covering
the GUI tray mode this setup uses) into a venv created with
`--system-site-packages` so the apt-installed PyGObject (`gi`) is visible to
the GTK clipboard path and tray backend. If it exits with `ERROR: expected
requirements file not found`, see `docs/troubleshooting.md`.

### B2. Configure the client (DATA file)

The client reads its config from a JSON file literally named `DATA`, placed
next to `main.py` — i.e. `vendor/ClipCascade_Desktop/src/DATA` (confirmed
from `core/constants.py`'s `DATA_FILE_NAME = "DATA"` and
`get_program_files_directory()`, which on Linux resolves to the directory
one level above `core/`, i.e. `src/`).

```bash
cp linux/DATA.template vendor/ClipCascade_Desktop/src/DATA
```

Edit `vendor/ClipCascade_Desktop/src/DATA` and:

1. Delete the `_comment_` key (JSON has no comment syntax; that key only
   exists in the template to explain the others).
2. Replace `SERVER_LAN_IP` in both `server_url` and `websocket_url` with the
   IP from step A5.
3. Set `username` to the non-admin sync user from step A4.
4. Set `salt` to a value of your choosing — **it must be byte-for-byte
   identical to `macos/DATA.template`'s `salt` on the Mac**, along with
   `hash_rounds` (default `664937`), or the two clients cannot decrypt each
   other's clipboard payloads (E2E key = login password + salt, run through
   `hash_rounds` iterations).
5. Confirm `enable_image_sharing` and `enable_file_sharing` are both
   `false` (text-only v1 scope; this must match on the macOS client too —
   the toggle only blocks *sending*, so both ends need it off).

The `DATA` file is gitignored (it holds derived key material and the
server's LAN IP) — do not commit it.

### B3. Install the systemd user unit (autostart)

```bash
./linux/install.sh
```

This substitutes the real repo path into `linux/clipcascade-client.service`,
installs it to `~/.config/systemd/user/clipcascade-client.service`, then
runs:

```bash
systemctl --user daemon-reload
systemctl --user enable --now clipcascade-client.service
```

The unit's `ExecStart` runs the client with `--gui true --polling 1`:

- `--gui true` forces the AppIndicator/GTK tray path (GNOME/Wayland
  defaults to CLI mode otherwise).
- `--polling 1` overrides the client's default 3-second clipboard poll
  interval down to 1s — needed to stay inside this project's ~2s sync
  latency budget on the polling fallback path (see
  `docs/troubleshooting.md` for when polling vs. `wl-paste --watch` is
  actually used).

`WantedBy=graphical-session.target` means this starts automatically at
every graphical login — no manual start needed after a reboot (AC4).

### B4. First run / login

The very first time the client starts (either via `./linux/install.sh`
above, or by running it manually for a dry run:
`.venv-linux/bin/python3 vendor/ClipCascade_Desktop/src/main.py --gui true --polling 1`),
a login window appears, pre-filled from the `DATA` file with `server_url`
and `username`. Enter the sync user's password and submit.

- Success shows a dialog: *"Success! ClipCascade will now run in the task
  bar/menu bar."* and a tray icon should appear.
- *"Login successful but websocket connection failed"* almost always means
  `server_url`/`websocket_url` still points at `localhost` instead of the
  LAN IP — see A5 and `docs/troubleshooting.md`.

On success the client persists a session cookie + hashed password back
into `DATA`, so subsequent restarts (including via the systemd unit) skip
the login window.

### B5. Verify

```bash
systemctl --user status clipcascade-client.service
journalctl --user -u clipcascade-client.service -f
```

Check the AppIndicator tray icon is visible in the top bar. Then follow
`docs/UAT-checklist.md` for the actual cross-machine sync tests (AC2/AC3),
concealed-item test (AC5), offline test (AC6), and non-text test (AC7).

## Known issues affecting this setup

See `docs/troubleshooting.md` for: reconnect loops after restarting the
server container, missing tray icon, the localhost/websocket failure, quit
hangs (macOS side), and latency/log-level gotchas.
