import logging
import re
import subprocess
import threading
import time

from core.constants import *
from clipboard.sensitive import should_skip_mime_targets

_callback_update = None
_clipboard_thread = None

_block_image_once = False

_is_gdk_running = False
_run_poll = threading.Event()
_wl_watch_proc = None

_inspection_unavailable_warned = False


def _warn_inspection_unavailable_once(detail=""):
    """Log the fail-closed warning (AC5) exactly once per process lifetime,
    across all Linux clipboard-monitoring code paths in this module (GTK,
    xclip/wl-paste polling, wl-paste --watch) -- otherwise a persistent
    inspection failure would log every poll tick forever."""
    global _inspection_unavailable_warned
    if not _inspection_unavailable_warned:
        _inspection_unavailable_warned = True
        suffix = f" ({detail})" if detail else ""
        logging.warning(
            "clipboard type inspection unavailable -- failing closed, "
            f"items will NOT be synced until this is resolved{suffix}"
        )


def _gtk_clipboard_targets(clipboard):
    """Best-effort read of the GTK clipboard's advertised targets, via
    Gtk.Clipboard.wait_for_targets() (gtk_clipboard_wait_for_targets).

    Returns None on any failure. Callers MUST treat None as "could not
    inspect" and fail CLOSED (should_skip_mime_targets(None) -> True, AC5)
    -- never as "assume non-sensitive, send it". A persistent failure here
    means the monitor stops sending anything at all until resolved; that is
    the intended fail-safe behavior, not a bug.
    """
    try:
        success, targets = clipboard.wait_for_targets()
        if not success or not targets:
            return []
        return [t.name() for t in targets]
    except Exception as e:
        _warn_inspection_unavailable_once(str(e))
        return None


def _on_clipboard_changed(
    clipboard, event=None, enable_image_monitoring=False, enable_file_monitoring=False
):
    global _block_image_once

    # Password-manager-owned content (e.g. KeePassXC sets
    # x-kde-passwordManagerHint) must never be synced. This is the PRE-read
    # check. Each branch below re-checks again AFTER its own content read
    # and requires BOTH checks to be clean before calling _callback_update
    # -- a single pre-read check is a TOCTOU gap: a sensitive item copied in
    # the moment between this check and the actual wait_for_text/wait_for_
    # uris/wait_for_image read would otherwise be sent using this stale
    # clean verdict. should_skip_mime_targets(None) is True (AC5 fails
    # CLOSED), so an inspection failure on either side skips.
    skip_before = should_skip_mime_targets(_gtk_clipboard_targets(clipboard))

    # Files
    if enable_file_monitoring:
        uris = clipboard.wait_for_uris()
        if uris is not None and len(uris) > 0:
            if _callback_update:
                skip_after = should_skip_mime_targets(_gtk_clipboard_targets(clipboard))
                if skip_before or skip_after:
                    logging.debug("skipped concealed/transient clipboard item")
                else:
                    _callback_update("files", uris)
            return

    # Text
    text = clipboard.wait_for_text()
    if text is not None and len(text) > 0:
        if _callback_update:
            skip_after = should_skip_mime_targets(_gtk_clipboard_targets(clipboard))
            if skip_before or skip_after:
                logging.debug("skipped concealed/transient clipboard item")
            else:
                _callback_update("text", text)
        return

    # Image
    if enable_image_monitoring:
        pixbuf = clipboard.wait_for_image()
        if pixbuf is not None:
            if _block_image_once:
                _block_image_once = False
                return
            if _callback_update:
                skip_after = should_skip_mime_targets(_gtk_clipboard_targets(clipboard))
                if skip_before or skip_after:
                    logging.debug("skipped concealed/transient clipboard item")
                    return
                success, buffer = pixbuf.save_to_bufferv("png")
                if success:
                    _callback_update("image", bytes(buffer))
                else:
                    logging.error("Failed to convert image(pixbuf) to buffer")
                return


def _list_mime_targets(x_mode: bool):
    """Best-effort read + parse of the clipboard's currently advertised
    MIME/X targets (`xclip -t TARGETS` / `wl-paste -l`). Used both as the
    per-tick PRE-read check and, called again, as the post-read re-check
    (F1/TOCTOU) in _monitor_x_wl_clipboard and _monitor_wl_watch (the latter
    always passes x_mode=False since it is wl-paste-only).

    Returns None on failure. Callers MUST treat None as "could not inspect"
    and fail CLOSED via should_skip_mime_targets(None) -> True (AC5) --
    never as "assume non-sensitive, send it".
    """
    if x_mode:
        success, raw = execute_command(
            "xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"
        )
    else:
        success, raw = execute_command("wl-paste", "-l")
    if not success:
        _warn_inspection_unavailable_once(raw if isinstance(raw, str) else str(raw))
        return None
    mime_list = raw.decode("utf-8")
    mime_list = mime_list.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [m.strip() for m in mime_list if len(m.strip()) > 0]


def _monitor_x_wl_clipboard(
    x_mode: bool,
    enable_image_monitoring=False,
    enable_file_monitoring=False,
):
    global _block_image_once
    last_error = None
    previous_clipboard: str | bytes | None = None
    ignore_patterns = [
        r"target .+ not available",  # xclip pattern
        r"no suitable type of content copied",  # wl-clipboard pattern
    ]

    if LINUX_CLIPBOARD_POLL_INTERVAL_SEC is not None:
        timeout = LINUX_CLIPBOARD_POLL_INTERVAL_SEC
        logging.info(f"Clipboard polling interval (--polling): {timeout}s")
    elif x_mode:
        timeout = 0.3  # xclip seconds
    else:
        timeout = 3  # wl-clipboard seconds

    while _run_poll.is_set():
        # PRE-read target check. See the post-read re-check after each
        # content read below -- a single pre-read check here is a TOCTOU
        # gap: a sensitive item copied between this check and the actual
        # content read would otherwise be sent using this stale clean
        # verdict. should_skip_mime_targets(None) is True (AC5 fails
        # CLOSED), so a target-listing failure on either side skips.
        mime_list = _list_mime_targets(x_mode)
        if mime_list is None:
            error_msg = "Failed to retrieve MIME types (clipboard type inspection unavailable)"
            if error_msg != last_error:
                logging.error(error_msg)
                last_error = error_msg
            time.sleep(timeout)
            continue

        type_ = convert_mime_to_generic_type(mime_list)
        skip_before = should_skip_mime_targets(mime_list)

        # Text
        if type_ == "text":
            if x_mode:
                success, text = execute_command(
                    "xclip", "-selection", "clipboard", "-o"
                )
            else:
                success, text = execute_command("wl-paste", "-n")
            if success:
                text = text.decode("utf-8")
                if len(text) > 0 and text != previous_clipboard:
                    previous_clipboard = text
                    if _callback_update:
                        skip_after = should_skip_mime_targets(
                            _list_mime_targets(x_mode)
                        )
                        if skip_before or skip_after:
                            logging.debug("skipped concealed/transient clipboard item")
                        else:
                            _callback_update("text", text)
            else:
                error_msg = f"Failed to retrieve text content from clipboard. {text}"
                if error_msg != last_error:
                    if not any(
                        re.search(pattern, error_msg.lower())
                        for pattern in ignore_patterns
                    ):
                        logging.error(error_msg)
                    last_error = error_msg

        # Image
        if type_ == "image" and enable_image_monitoring:
            if x_mode:
                success, image = execute_command(
                    "xclip",
                    "-selection",
                    "clipboard",
                    "-t",
                    "image/png",
                    "-o",
                )
            else:
                success, image = execute_command("wl-paste", "-t", "image/png")
            if success:
                if image != previous_clipboard:
                    previous_clipboard = image
                    if _block_image_once:
                        _block_image_once = False
                    elif _callback_update:
                        skip_after = should_skip_mime_targets(
                            _list_mime_targets(x_mode)
                        )
                        if skip_before or skip_after:
                            logging.debug("skipped concealed/transient clipboard item")
                        else:
                            _callback_update("image", image)
            else:
                error_msg = f"Failed to retrieve image content from clipboard. {image}"
                if error_msg != last_error:
                    if not any(
                        re.search(pattern, error_msg.lower())
                        for pattern in ignore_patterns
                    ):
                        logging.error(error_msg)
                    last_error = error_msg

        # Files
        if type_ == "files" and enable_file_monitoring:
            if x_mode:
                success, files = execute_command(
                    "xclip",
                    "-selection",
                    "clipboard",
                    "-t",
                    "text/uri-list",
                    "-o",
                )
            else:
                success, files = execute_command(
                    "wl-paste", "-t", "text/uri-list", "-n"
                )
            if success:
                files = files.decode("utf-8")
                files = files.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                files = [f.strip() for f in files if len(f.strip()) > 0]
                if files != previous_clipboard:
                    previous_clipboard = files
                    if _callback_update:
                        skip_after = should_skip_mime_targets(
                            _list_mime_targets(x_mode)
                        )
                        if skip_before or skip_after:
                            logging.debug("skipped concealed/transient clipboard item")
                        else:
                            _callback_update("files", files)
            else:
                error_msg = f"Failed to retrieve files content from clipboard. {files}"
                if error_msg != last_error:
                    if not any(
                        re.search(pattern, error_msg.lower())
                        for pattern in ignore_patterns
                    ):
                        logging.error(error_msg)
                    last_error = error_msg

        time.sleep(timeout)


def _monitor_wl_watch(enable_image_monitoring=False, enable_file_monitoring=False):
    """Event-driven Wayland clipboard monitoring using wl-paste --watch.
    Uses the wlr-data-control-v1 protocol which does not create visible
    surfaces or steal focus. Supported by wlroots-based compositors
    (Sway, Hyprland, etc.) and KDE Plasma on Wayland.
    Returns True if watch mode ran successfully, False to fall back to polling."""
    global _block_image_once, _wl_watch_proc

    last_error = None
    previous_clipboard = None
    ignore_patterns = [
        r"target .+ not available",
        r"no suitable type of content copied",
    ]

    try:
        _wl_watch_proc = subprocess.Popen(
            ["wl-paste", "--watch", "echo"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        time.sleep(0.5)
        if _wl_watch_proc.poll() is not None:
            stderr_out = _wl_watch_proc.stderr.read().decode("utf-8", errors="ignore")
            logging.warning(
                f"wl-paste --watch exited immediately: {stderr_out.strip()}"
            )
            _wl_watch_proc = None
            return False

        logging.info(
            "Using wl-paste --watch for clipboard monitoring (no focus stealing)"
        )

        while _run_poll.is_set():
            line = _wl_watch_proc.stdout.readline()
            if not line:
                break
            if not _run_poll.is_set():
                break

            # PRE-read target check. See the post-read re-check after each
            # content read below -- a single pre-read check here is a
            # TOCTOU gap: a sensitive item copied between this check and the
            # actual content read would otherwise be sent using this stale
            # clean verdict. should_skip_mime_targets(None) is True (AC5
            # fails CLOSED), so a target-listing failure on either side
            # skips.
            mime_list = _list_mime_targets(x_mode=False)
            if mime_list is None:
                error_msg = "Failed to retrieve MIME types (clipboard type inspection unavailable)"
                if error_msg != last_error:
                    logging.error(error_msg)
                    last_error = error_msg
                continue

            type_ = convert_mime_to_generic_type(mime_list)
            skip_before = should_skip_mime_targets(mime_list)

            # Text
            if type_ == "text":
                success, text = execute_command("wl-paste", "-n")
                if success:
                    text = text.decode("utf-8")
                    if len(text) > 0 and text != previous_clipboard:
                        previous_clipboard = text
                        if _callback_update:
                            skip_after = should_skip_mime_targets(
                                _list_mime_targets(x_mode=False)
                            )
                            if skip_before or skip_after:
                                logging.debug("skipped concealed/transient clipboard item")
                            else:
                                _callback_update("text", text)
                else:
                    error_msg = (
                        f"Failed to retrieve text content from clipboard. {text}"
                    )
                    if error_msg != last_error:
                        if not any(
                            re.search(pattern, error_msg.lower())
                            for pattern in ignore_patterns
                        ):
                            logging.error(error_msg)
                        last_error = error_msg

            # Image
            elif type_ == "image" and enable_image_monitoring:
                success, image = execute_command("wl-paste", "-t", "image/png")
                if success:
                    if image != previous_clipboard:
                        previous_clipboard = image
                        if _block_image_once:
                            _block_image_once = False
                        elif _callback_update:
                            skip_after = should_skip_mime_targets(
                                _list_mime_targets(x_mode=False)
                            )
                            if skip_before or skip_after:
                                logging.debug("skipped concealed/transient clipboard item")
                            else:
                                _callback_update("image", image)
                else:
                    error_msg = (
                        f"Failed to retrieve image content from clipboard. {image}"
                    )
                    if error_msg != last_error:
                        if not any(
                            re.search(pattern, error_msg.lower())
                            for pattern in ignore_patterns
                        ):
                            logging.error(error_msg)
                        last_error = error_msg

            # Files
            elif type_ == "files" and enable_file_monitoring:
                success, files = execute_command(
                    "wl-paste", "-t", "text/uri-list", "-n"
                )
                if success:
                    files = files.decode("utf-8")
                    files = (
                        files.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    )
                    files = [f.strip() for f in files if len(f.strip()) > 0]
                    if files != previous_clipboard:
                        previous_clipboard = files
                        if _callback_update:
                            skip_after = should_skip_mime_targets(
                                _list_mime_targets(x_mode=False)
                            )
                            if skip_before or skip_after:
                                logging.debug("skipped concealed/transient clipboard item")
                            else:
                                _callback_update("files", files)
                else:
                    error_msg = (
                        f"Failed to retrieve files content from clipboard. {files}"
                    )
                    if error_msg != last_error:
                        if not any(
                            re.search(pattern, error_msg.lower())
                            for pattern in ignore_patterns
                        ):
                            logging.error(error_msg)
                        last_error = error_msg

        return True
    except FileNotFoundError:
        logging.warning("wl-paste not found, cannot use --watch mode")
        return False
    except Exception as e:
        logging.warning(f"wl-paste --watch failed: {e}")
        return False
    finally:
        if _wl_watch_proc is not None:
            _wl_watch_proc.terminate()
            try:
                _wl_watch_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _wl_watch_proc.kill()
            _wl_watch_proc = None


def convert_mime_to_generic_type(mime_list):
    if "text/uri-list" in mime_list:
        return "files"

    if any(mime.startswith("image/") for mime in mime_list):
        return "image"

    text_mime = [
        "text/plain",
        "text/plain;charset=utf-8",
        "STRING",
        "TEXT",
        "COMPOUND_TEXT",
        "UTF8_STRING",
    ]
    if any(t_mime in mime_list for t_mime in text_mime):
        return "text"

    return "unknown"


def execute_command(*args) -> tuple:
    """
    Executes a command with the given arguments and returns the output or error.

    Parameters:
        *args: Variable-length argument list to be passed as the command and arguments.

    Returns:
        tuple: (success: bool, result: str)
               success is True if the command executed successfully, False otherwise.
               result is the output or error of the command.
    """
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, error = process.communicate()
    if process.returncode == 0:  # Success
        return (True, output)
    else:  # Failure
        return (False, error.decode())


def is_x_clipboard_owner():
    # Check if the X clipboard is owned by the current user
    return execute_command("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o")[0]


def _start_clipboard_polling(enable_image_monitoring, enable_file_monitoring):
    if XMODE:
        x_clipboard_owner = is_x_clipboard_owner()
        if not x_clipboard_owner:
            logging.warning(
                "x-clip is not owned by the current user. Switching to wl-clipboard."
            )
        _monitor_x_wl_clipboard(
            x_mode=x_clipboard_owner,
            enable_image_monitoring=enable_image_monitoring,
            enable_file_monitoring=enable_file_monitoring,
        )
    else:
        if not _monitor_wl_watch(
            enable_image_monitoring=enable_image_monitoring,
            enable_file_monitoring=enable_file_monitoring,
        ):
            logging.info(
                "Falling back to wl-paste polling mode for clipboard monitoring"
            )
            _monitor_x_wl_clipboard(
                x_mode=False,
                enable_image_monitoring=enable_image_monitoring,
                enable_file_monitoring=enable_file_monitoring,
            )


def _runner(enable_image_monitoring=False, enable_file_monitoring=False):
    global _is_gdk_running, _run_poll
    logging.info(f"XMODE: {XMODE}")
    try:
        _run_poll.set()
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gtk, Gdk

        if "x11" in str(type(Gdk.Display.get_default())).lower():  # X11
            logging.info("Starting GTK clipboard monitoring for X11 display server.")
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.connect(
                "owner-change",
                lambda clip, event: _on_clipboard_changed(
                    clip, event, enable_image_monitoring, enable_file_monitoring
                ),
            )
            _is_gdk_running = True
            Gtk.main()
        else:
            logging.warning(
                f"Unsupported display server detected ${str(type(Gdk.Display.get_default())).lower()}. Starting polling mode for {detect_linux_display_server()} server as fallback."
            )
            _start_clipboard_polling(enable_image_monitoring, enable_file_monitoring)
    except Exception as e:
        logging.error(
            f"Failed to start clipboard monitor: Error {e}\nStarting polling mode for {detect_linux_display_server()} server as fallback."
        )
        _start_clipboard_polling(enable_image_monitoring, enable_file_monitoring)


def _start(enable_image_monitoring=False, enable_file_monitoring=False):
    global _clipboard_thread
    if not _clipboard_thread:
        _clipboard_thread = threading.Thread(
            target=_runner,
            args=(enable_image_monitoring, enable_file_monitoring),
            daemon=True,
        )
        _clipboard_thread.start()


def stop():
    global _clipboard_thread, _callback_update, _block_image_once, _run_poll, _is_gdk_running, _wl_watch_proc
    if _clipboard_thread:
        if _is_gdk_running:
            import gi

            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk

            Gtk.main_quit()
            _is_gdk_running = False
        _run_poll.clear()
        if _wl_watch_proc is not None:
            _wl_watch_proc.terminate()
        _clipboard_thread.join()  # Wait for the thread to finish
        _clipboard_thread = None
        _callback_update = None
        _block_image_once = False
        _wl_watch_proc = None
        logging.info("Clipboard monitor stopped")


def wait():
    global _clipboard_thread
    if _clipboard_thread:
        _clipboard_thread.join()


def enable_block_image_once():
    global _block_image_once
    _block_image_once = True


def on_update(callback, enable_image_monitoring=False, enable_file_monitoring=False):
    global _callback_update
    _callback_update = callback
    _start(enable_image_monitoring, enable_file_monitoring)
