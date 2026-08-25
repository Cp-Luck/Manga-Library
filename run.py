"""Start the backend: python run.py"""

import atexit
import re
import shutil
import socket
import subprocess
import sys
import threading

import uvicorn

# Windows consoles often default to cp1252, which can't encode the em dashes
# in these messages or the block characters the QR code prints — force UTF-8
# so output never crashes the process before the server even starts.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "0.0.0.0"
PORT = 8000

# Fall back to the default winget install path if cloudflared isn't on PATH
# yet in this shell (installers often need a fresh terminal to be picked up).
CLOUDFLARED_CANDIDATES = [
    "cloudflared",
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
]

TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def local_ip() -> str:
    """Best-effort LAN IP, so we can print a URL your phone can actually reach."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(
            ("8.8.8.8", 80)
        )  # no packet actually sent, just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def find_cloudflared():
    for candidate in CLOUDFLARED_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return None


def start_tunnel(cloudflared_path: str):
    """Launches a cloudflared quick tunnel in the background and waits for
    it to print its public URL. Returns None on timeout so the caller can
    fall back to manual instructions instead of hanging startup forever."""
    proc = subprocess.Popen(
        [cloudflared_path, "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    atexit.register(
        proc.terminate
    )  # don't leave the tunnel running after the server stops

    found = {}

    def read_output():
        for line in proc.stdout:
            match = TUNNEL_URL_RE.search(line)
            if match:
                found["url"] = match.group(0)
                return

    thread = threading.Thread(target=read_output, daemon=True)
    thread.start()
    thread.join(timeout=20)
    return found.get("url")


def print_qr(url: str):
    """Best-effort — a display quirk here should never take the whole
    server down with it, so failures are swallowed, not raised."""
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii()
    except Exception as e:  # noqa: BLE001 — deliberately broad: see docstring
        print(f"  (couldn't render QR code: {e})", flush=True)


def print_field(label: str, value: str):
    print(f"  {(label + ':').ljust(12)}{value}", flush=True)


if __name__ == "__main__":
    ip = local_ip()
    print_field("Local", f"http://localhost:{PORT}")
    print_field("Network", f"http://{ip}:{PORT}  (same Wi-Fi)")
    print_field("Docs", f"http://localhost:{PORT}/docs")
    print_field(
        "Collection",
        f"http://{ip}:{PORT}/collection  (browsing works fine over plain http/LAN)",
    )
    print(flush=True)

    cloudflared_path = find_cloudflared()
    if cloudflared_path:
        print(
            "  Starting HTTPS tunnel (only needed to scan barcodes — camera access requires it)...",
            flush=True,
        )
        tunnel_url = start_tunnel(cloudflared_path)
        if tunnel_url:
            print_field("Phone", tunnel_url)
            print(flush=True)
            print_qr(tunnel_url)
        else:
            print(
                "  Tunnel didn't come up in time — run it manually in another terminal:",
                flush=True,
            )
            print(f"    cloudflared tunnel --url http://localhost:{PORT}", flush=True)
    else:
        print(
            "  cloudflared not found — install it for one-command phone testing, or run",
            flush=True,
        )
        print("  manually in another terminal:", flush=True)
        print(f"    cloudflared tunnel --url http://localhost:{PORT}", flush=True)
    print(flush=True)

    uvicorn.run("app.backend.main:app", host=HOST, port=PORT, reload=True)
