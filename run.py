import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from app import create_app, socketio
from app.api.piUtils import cleanupGpio

app = create_app()


def wait_for_server(url, timeout=15):
    """Wait until the HTTP server is responsive."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status in (200, 302, 404):
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def open_browser(url="http://localhost:5050"):
    """
    Automatically opens the dashboard URL on the Raspberry Pi display.
    Configures environment variables (DISPLAY, WAYLAND_DISPLAY, XDG_RUNTIME_DIR)
    so the browser opens properly whether executed directly from desktop,
    SSH, or a startup service.
    """
    # Wait until the web server is ready to accept requests
    wait_for_server(url, timeout=15)

    # Set up display environment variables for GUI on the RPi display
    env = os.environ.copy()
    if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
        env["DISPLAY"] = ":0"
        os.environ.setdefault("DISPLAY", ":0")

    if "XDG_RUNTIME_DIR" not in env:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        user_runtimes = [f"/run/user/{uid}", "/run/user/1000", "/run/user/1001"]
        for runtime_path in user_runtimes:
            if os.path.exists(runtime_path):
                env["XDG_RUNTIME_DIR"] = runtime_path
                os.environ.setdefault("XDG_RUNTIME_DIR", runtime_path)
                if os.path.exists(os.path.join(runtime_path, "wayland-0")):
                    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
                    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
                break

    kiosk_mode = "--kiosk" in sys.argv or os.environ.get("KIOSK", "0").lower() in ("1", "true")

    # On Linux / Raspberry Pi, prefer Chromium (the standard RPi browser)
    chromium_bin = shutil.which("chromium-browser") or shutil.which("chromium")
    if chromium_bin and sys.platform.startswith("linux"):
        cmd = [
            chromium_bin,
            "--no-first-run",
            "--disable-restore-session-state",
            "--disable-infobars",
        ]
        # Chromium requires --no-sandbox if run as root (e.g. sudo python run.py)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.append("--no-sandbox")

        if kiosk_mode:
            cmd.append("--kiosk")
        else:
            cmd.append("--start-maximized")

        cmd.append(url)

        try:
            print(f"[BROWSER] Launching {url} on RPi display with Chromium...")
            subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception as e:
            print(f"[BROWSER] Chromium direct launch failed ({e}), falling back to default browser...")

    # Fallback to standard Python webbrowser (cross-platform, works on Mac/Windows/Linux)
    try:
        print(f"[BROWSER] Opening {url} in default browser...")
        webbrowser.open(url)
    except Exception as e:
        print(f"[BROWSER] Failed to open browser: {e}")


def start_browser_thread(url="http://localhost:5050"):
    """Starts the browser opening thread if not explicitly disabled."""
    if "--no-browser" in sys.argv or os.environ.get("NO_BROWSER", "0").lower() in ("1", "true"):
        print("[BROWSER] Auto-open browser is disabled (--no-browser or NO_BROWSER=1).")
        return None

    thread = threading.Thread(target=open_browser, args=(url,), daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    start_browser_thread("http://localhost:5050")
    try:
        socketio.run(app, host="0.0.0.0", port=5050, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        pass
    finally:
        cleanupGpio()
