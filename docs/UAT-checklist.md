# UAT checklist

Manual, two-physical-machine acceptance test for ClipCascade v3.2.0
self-hosted Mac⇄Ubuntu clipboard sync (text-only, E2E-encrypted). Do
`docs/setup-ubuntu.md` and `docs/setup-macos.md` first — both clients
should be logged in, running, and idle before starting this checklist.

Conventions used below:

- **Mac** = the MacBook, running the LaunchAgent-managed client.
- **Ubuntu** = the Ubuntu desktop, running both the server container and
  the systemd-managed client.
- Every section ends with a result line — check exactly one:
  - [ ] PASS
  - [ ] FAIL

---

## AC1 — Server on Ubuntu desktop, LAN-reachable from both machines, restarts automatically after desktop reboot

1. On **Ubuntu**: `cd server/ && docker compose ps` — confirm the
   `clipcascade` container shows `Up`.
2. On **Ubuntu**: get the LAN IP: `ip addr show | grep 'inet '`.
3. On **Mac**: `curl -sS -o /dev/null -w '%{http_code}\n' http://<ubuntu-lan-ip>:8080/login` —
   expect an HTTP response code (e.g. `200`), not a connection error/timeout.
4. On **Ubuntu**: physically reboot the machine (`sudo reboot`, or the
   power/restart menu).
5. After the machine finishes booting and you've logged in, wait ~30s, then
   from **Mac** repeat step 3.

**Expected:** step 3 returns a valid HTTP status both before and after the
reboot, with no manual `docker compose up` needed after the reboot (the
container's `restart: unless-stopped` policy plus `docker.service` being
enabled on boot handles this — see `docs/setup-ubuntu.md` A1–A2).

- [ ] PASS
- [ ] FAIL

---

## AC2 — Text copied on macOS is pasteable on Ubuntu within 2s, byte-identical (UTF-8 + whitespace preserved)

Test string (note the **trailing space** — copy it exactly, including that
space):

```
https://example.com/päge?q=1 
```

1. On **Mac**, put the exact test string on the clipboard, preserving the
   trailing space and the `ä` (do not use `echo` without care — it can
   normalize whitespace/encoding depending on shell config; `printf` does
   not):
   ```bash
   printf '%s' 'https://example.com/päge?q=1 ' | pbcopy
   ```
2. Start a stopwatch (or note the wall-clock time) the moment you run that
   command.
3. On **Ubuntu**, paste as soon as the tray icon indicates activity (or
   after ~1s), and stop the stopwatch the moment you run:
   ```bash
   wl-paste | xxd | tail -5
   ```
4. Confirm elapsed time from step 2 to step 3 is **under 2 seconds**.
5. Byte-identity check — compare hashes (read/photograph one side, compare
   to the other; there is no shared filesystem between the two physical
   machines):
   - Mac: `pbpaste | shasum -a 256`
   - Ubuntu: `wl-paste | sha256sum`
   - The two hashes must match exactly.
6. Also visually confirm in the `xxd` dump from step 3 that the last bytes
   are `20` (the trailing space, hex `20`) and that `ä` appears as its
   2-byte UTF-8 encoding (`c3 a4`), not a single byte or a `?`/mojibake
   substitution.

**Alternative latency method (scripted timestamp)** — if a stopwatch feels
too imprecise:

```bash
# Mac (sending):
printf 'latency-test %s' "$(date +%s.%N)" | pbcopy
```
```bash
# Ubuntu (receiving, run right after noticing the tray update):
wl-paste
# then, using the embedded timestamp T from the pasted text:
echo "$(date +%s.%N) - T" | bc   # must be < 2
```

- [ ] PASS
- [ ] FAIL

---

## AC3 — Text copied on Ubuntu is pasteable on macOS within 2s, byte-identical (UTF-8 + whitespace preserved)

Same test string and method as AC2, reversed direction.

1. On **Ubuntu**, start the stopwatch and copy:
   ```bash
   printf '%s' 'https://example.com/päge?q=1 ' | wl-copy
   ```
2. On **Mac**, stop the stopwatch when you paste:
   ```bash
   pbpaste | xxd | tail -5
   ```
3. Confirm elapsed time is **under 2 seconds**.
4. Byte-identity check:
   - Ubuntu: `wl-paste | sha256sum`
   - Mac: `pbpaste | shasum -a 256`
   - Hashes must match.
5. Confirm the trailing-space (`20`) and `ä` (`c3 a4`) bytes as in AC2 step 6.

- [ ] PASS
- [ ] FAIL

---

## AC4 — Both clients auto-start on login with a visible tray/menu-bar indicator; no manual start after reboot

1. On **Mac**: `sudo reboot` (or Restart from the Apple menu).
2. Log back in. **Do not** run `./macos/run-client.sh` or any other manual
   start command.
3. Confirm the ClipCascade menu-bar icon appears within a reasonable time
   after login, with no terminal opened and nothing run by hand.
4. On **Ubuntu**: `sudo reboot`.
5. Log back in. **Do not** run `linux/setup.sh`, `linux/install.sh`, or
   start the client by hand.
6. Confirm the ClipCascade AppIndicator tray icon appears in the top bar
   within a reasonable time after login.
7. Cross-check both autostart mechanisms report themselves as active:
   - Mac: `launchctl list | grep com.clipcascade.client`
   - Ubuntu: `systemctl --user status clipcascade-client.service` (should
     show `active (running)`)

**Expected:** both tray/menu-bar icons are visible after their respective
reboots, with zero manual intervention. (If Ubuntu's client is stuck from
the known reconnect-loop bug instead, that still counts as "started
automatically" for this AC — see `docs/troubleshooting.md` for the
separate reconnect-loop symptom, which is not what AC4 is testing.)

- [ ] PASS
- [ ] FAIL

---

## AC5 — Concealed/transient clipboard items (password managers) are NOT propagated

This requires a password manager that actually **marks** its clipboard
copies. Field-verified 2026-07-28 on macOS: use **KeePassXC** or the
**1Password 8 native app**. The following do NOT mark and WILL sync like
plain text — do not use them for this test, and know they are not protected
in daily use either:

- **Apple's Passwords app** — verified on the real pasteboard: it puts only
  `public.utf8-plain-text`, no concealed/transient marker of any kind.
- **Any browser-extension copy** (1Password/Bitwarden/etc. inside
  Chrome/Safari) — the copy goes through the browser clipboard API,
  markers never attach.
- **iPhone → Mac via Apple Universal Clipboard** — markers don't survive
  the Continuity hop, and the item then relays on to Linux.

On Linux, KeePassXC sets `x-kde-passwordManagerHint` and is detected.

1. On **Ubuntu** (or Mac — pick whichever has a password manager
   installed), open your password manager and **copy a password field**
   for any stored entry (e.g. KeePassXC: select an entry, press the copy-
   password button/shortcut).
2. On the **other machine**, attempt to paste (`pbpaste` / `wl-paste`, or
   paste into a scratch text field) within 5 seconds.
3. **Expected:** nothing new arrives — the paste target still shows
   whatever was there before step 1 (or errors/empty if the clipboard was
   never set on that machine). Confirm no crash and no error dialog on
   either client.
4. **Negative control (do this immediately after step 1–3, same
   session):** on the **same machine** you copied the secret from, now do
   a normal, non-password-manager copy — e.g.:
   ```bash
   printf '%s' 'AC5-negative-control-plain-copy' | pbcopy   # or wl-copy
   ```
5. On the **other machine**, paste again.

**Expected for step 5:** the plain-text negative-control string **does**
arrive this time, within the normal ~2s budget. This proves the skip in
step 1–3 was the concealed/transient detection working as intended, not a
broken/dead sync link (i.e. rules out "nothing synced because the client
was down").

Do **not** rely on grepping the client log for a confirmation line here —
see `docs/troubleshooting.md`'s "Concealed/transient skip isn't visibly
confirmable at default log level" section for why the `DEBUG`-level
`"skipped concealed/transient clipboard item"` message will not appear
under the default logging configuration even when the skip is working
correctly. Judge this AC by the observed clipboard behavior in steps 2–3
and 5, not by log contents.

- [ ] PASS
- [ ] FAIL

---

## AC6 — Server/peer unreachable → local copy/paste unaffected, no crash; after reconnect, the NEXT copy syncs (last-value-wins; missed copies are not replayed)

Run all three scenarios; all three must pass for this AC to pass.

### 6a. Stop the server container

1. On **Ubuntu**: `cd server/ && docker compose stop`.
2. On **both** machines, copy and paste locally within the same app
   (`pbcopy`/`pbpaste` on Mac, `wl-copy`/`wl-paste` on Ubuntu) — confirm
   local clipboard read/write still works normally and neither client
   crashes (check `launchctl list | grep com.clipcascade.client` /
   `systemctl --user status clipcascade-client.service` still show the
   process alive, not crashed — a stuck reconnect loop is expected and
   fine per `docs/troubleshooting.md`, a crash/exit is not).
3. While still stopped, copy something on **Mac** (e.g.
   `printf 'missed-copy-1' | pbcopy`). Do **not** paste it on Ubuntu yet.
4. On **Ubuntu**: `docker compose start`.
5. If either client is stuck reconnect-looping (known upstream bug — see
   `docs/troubleshooting.md`), manually kick it:
   - Ubuntu: `systemctl --user restart clipcascade-client.service`
   - Mac: `launchctl kickstart -k gui/$(id -u)/com.clipcascade.client`
6. Paste on **Ubuntu** now. **Expected:** the `missed-copy-1` value from
   step 3 is **not** what appears (it was made while unreachable and must
   not be replayed) — either nothing new arrives, or whatever was already
   there before is unchanged.
7. Now, with both clients reconnected, copy something **new** on Mac
   (`printf 'post-reconnect-copy' | pbcopy`) and paste on Ubuntu.
   **Expected:** this one **does** sync within the normal ~2s budget —
   proving reconnect recovered and only the next copy after reconnect
   propagates (last-value-wins).

### 6b. Peer machine unreachable (not the server)

1. Disconnect **Mac** from the LAN (turn off Wi-Fi, or enable Airplane
   Mode).
2. On **Ubuntu**, copy/paste locally — confirm it still works, no crash.
3. On **Mac**, copy/paste locally (offline) — confirm it still works, no
   crash.
4. Reconnect Mac's Wi-Fi.
5. Copy something new on Ubuntu; confirm it syncs to Mac within ~2s once
   reconnected (allow for the reconnect-loop workaround in step 6a.5 if
   needed).

### 6c. Reboot both machines

1. Reboot **Ubuntu** and **Mac** (can be sequential).
2. After both are back up and both clients have auto-started (AC4),
   confirm a fresh copy on one machine syncs to the other within ~2s.

- [ ] PASS
- [ ] FAIL

---

## AC7 — Non-text content is not synced / ignored cleanly (text-only config on both clients)

Both `macos/DATA.template` and `linux/DATA.template` ship
`enable_image_sharing: false` and `enable_file_sharing: false` — confirm
both machines' actual `DATA` files still have both set to `false` before
this test (a client only blocks **sending** non-text content when its own
toggle is off — both ends must have it off, not just one).

1. On **Mac**: take a screenshot (`Cmd+Shift+Ctrl+4`, then drag a region —
   this copies the screenshot directly to the clipboard as an image
   instead of saving a file).
2. On **Ubuntu**, attempt to paste into an image-capable app (e.g. GIMP,
   or a browser's paste-image-capable input) within 5 seconds.
3. **Expected:** no image arrives on Ubuntu; neither client logs an error
   or crashes (check both are still running per AC6's process checks).
4. Reverse direction: on **Ubuntu**, copy a screenshot to the clipboard
   (e.g. GNOME's screenshot tool, `Print Screen` region-select, which
   copies to clipboard) or run `wl-copy < some-image.png`.
5. On **Mac**, attempt to paste into an image-capable app (Preview, or
   `Cmd+V` into a Mail draft) within 5 seconds.
6. **Expected:** no image arrives on Mac; neither client crashes.
7. As a sanity check that the link itself is still alive, do one more
   plain-text copy/paste in either direction and confirm it still syncs
   (rules out "nothing synced because the client died", not because
   images are correctly filtered).

- [ ] PASS
- [ ] FAIL

---

## AC8 — Repo reproducible from scratch

This AC is satisfied by `docs/setup-ubuntu.md` and `docs/setup-macos.md`
themselves plus this checklist. To validate it directly:

1. On a clean checkout of this repo (or a fresh clone), follow
   `docs/setup-ubuntu.md` Part A and Part B end to end on the Ubuntu
   machine, from "Install Docker" through "Verify", noting every step that
   required a manual workaround not already called out in that doc or in
   `docs/troubleshooting.md`.
2. On a clean checkout on the Mac, follow `docs/setup-macos.md` end to
   end.
3. Run AC1–AC7 above against the result.

**Expected:** every step in `docs/setup-ubuntu.md` / `docs/setup-macos.md`
either works as written, or fails in exactly the ways already documented in
`docs/troubleshooting.md` (with a working documented workaround) — no
*undocumented* manual intervention should be required to reach a working
sync setup.

- [ ] PASS
- [ ] FAIL
