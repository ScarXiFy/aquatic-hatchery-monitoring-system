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
from app.socketio_events import wait_for_client_connection

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


def find_browser_binary():
    """
    Finds a Chromium or Chrome binary across Linux/Raspberry Pi, macOS, and Windows.
    Returns the executable path if found, or None.
    """
    # 1. Common Linux / Raspberry Pi / system PATH executables
    for name in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable", "chrome"):
        path = shutil.which(name)
        if path:
            return path

    # 2. macOS application bundles
    if sys.platform == "darwin":
        mac_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium"),
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            os.path.expanduser("~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            os.path.expanduser("~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
        for path in mac_paths:
            if os.path.exists(path):
                return path

    # 3. Windows Program Files locations
    if sys.platform == "win32":
        win_paths = [
            os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
        for path in win_paths:
            if os.path.exists(path):
                return path

    return None


def open_browser(url="http://localhost:5050", client_timeout=2.0):
    """
    Automatically opens the dashboard URL in maximized/fullscreen mode on the display.
    Configures environment variables (DISPLAY, WAYLAND_DISPLAY, XDG_RUNTIME_DIR)
    so the browser opens properly whether executed directly from desktop,
    SSH, or a startup service.

    If the website is already open in a browser tab (e.g. from before the program
    was stopped with Ctrl+C), it reuses the open tab instead of launching a new one.
    """
    # Wait until the web server is ready to accept requests
    if not wait_for_server(url, timeout=15):
        print(f"[BROWSER] Server at {url} did not become responsive in time.")
        return

    # Check if a client tab is already open (or reconnects within client_timeout)
    force_new = "--new-tab" in sys.argv or "--force-browser" in sys.argv
    if not force_new:
        timeout = float(os.environ.get("BROWSER_CLIENT_TIMEOUT", str(client_timeout)))
        if wait_for_client_connection(timeout=timeout):
            print(f"[BROWSER] Existing web app tab detected for {url}. Reusing already open tab.")
            return

    # Set up display environment variables for GUI on the display
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

    # Prefer Chromium / Chrome across platforms for maximized/fullscreen support
    browser_bin = find_browser_binary()
    if browser_bin:
        cmd = [
            browser_bin,
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
            cmd.append("--start-fullscreen")

        cmd.append(url)

        try:
            print(f"[BROWSER] Launching {url} in maximized/fullscreen mode with {os.path.basename(browser_bin)}...")
            subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception as e:
            print(f"[BROWSER] Direct browser launch failed ({e}), falling back to default browser...")

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
