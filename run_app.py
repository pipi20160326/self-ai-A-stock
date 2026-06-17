from __future__ import annotations

import socket
import sys
import webbrowser
from pathlib import Path

from streamlit.web import cli as stcli


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def pick_port(start: int = 8501) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> None:
    app_path = resource_path("app.py")
    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    webbrowser.open(url)
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--global.developmentMode",
        "false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
