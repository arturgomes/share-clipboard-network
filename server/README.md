# ClipCascade server (Ubuntu desktop)

Runs the ClipCascade server in Docker on the Ubuntu desktop. The two clients
(macOS + Ubuntu) connect to this over the LAN.

## 1. Install Docker

Either path works; pick one:

- **Ubuntu's own packages (simplest):**
  ```bash
  sudo apt update
  sudo apt install docker.io docker-compose-v2
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"   # log out/in (or `newgrp docker`) for this to take effect
  ```
- **Docker's official `docker-ce` repo** (newer Docker/Compose releases,
  more setup steps): follow
  https://docs.docker.com/engine/install/ubuntu/ if you need a version newer
  than what Ubuntu ships.

Before pulling the image, check
https://hub.docker.com/r/sathvikrao/clipcascade/tags to confirm a build
exists for your host's architecture (amd64 vs arm64) — this compose file
pins `3.2.0` but does not resolve the arch question for you.

## 2. Bring the server up

From this directory:

```bash
docker compose up -d
```

This starts the container on port `8080` and creates `./cc_users/` next to
this file to hold the H2 database (gitignored — do not commit it, it will
contain user credentials).

Ubuntu Desktop ships with `ufw` **inactive** by default, so port `8080` is
already reachable on the LAN. If `ufw` is enabled on this host, open the
port explicitly, or the other client won't be able to reach the server:

```bash
sudo ufw allow 8080/tcp
```

Check it's up:

```bash
docker compose ps
docker compose logs -f
```

## 3. First-login hardening (do this immediately)

ClipCascade ships with signup disabled and a default admin account:

- username: `admin`
- password: `admin123`

1. Open `http://<ubuntu-lan-ip>:8080` in a browser.
2. Log in as `admin` / `admin123`.
3. **Change the admin password immediately** — this default is public
   knowledge (it's in the project's own docs) and the server has no other
   access control in front of it on the LAN.

## 4. Create the non-admin sync user

The Mac and the Ubuntu desktop should sync as one regular (non-admin) user,
not as `admin`. Create it from the `/dashboard` admin panel after logging in.

**Note:** the exact click-path for user creation in the 3.2.0 dashboard is
not documented upstream and was not verified live as part of this task —
expect to find it under an "Users" or "Admin" section of `/dashboard`. If it
isn't obvious, check the ClipCascade GitHub README/wiki for screenshots
matching this version, or open an issue upstream.

Whatever username you create here is what goes into `server_url` /
`username` in `macos/DATA.template` and `linux/DATA.template` (see those
files' comments for the full key list, since the client's `DATA` file is
plain JSON and can't hold explanatory comments itself).

## 5. Give the server a stable LAN address

Clients must be configured with the Ubuntu box's **LAN IP**, never
`localhost` — pointing a client at `localhost` fails at the WebSocket layer
(see upstream issue
[Sathvik-Rao/ClipCascade#129](https://github.com/Sathvik-Rao/ClipCascade/issues/129)).
There is no mDNS/`.local` discovery in ClipCascade, so the IP has to be
fixed one way or another:

- Set a **DHCP reservation** for the Ubuntu desktop's MAC address in your
  router, so it always gets the same IP, or
- Assign a **static IP** on the Ubuntu box itself (netplan / NetworkManager).

Either way, note the resulting IP — it goes into `websocket_url` /
`server_url` in both client `DATA.template` files as `SERVER_LAN_IP`.

## Notes

- The server itself speaks plain HTTP/WebSocket — there is no built-in TLS.
  This is treated as acceptable for a home-LAN-only deployment; don't port-
  forward 8080 to the internet without adding a reverse proxy/VPN in front.
- `CC_MAX_MESSAGE_SIZE_IN_MiB=1` caps a single clipboard message at 1 MiB,
  which is generous for text-only sync (v1 scope).
