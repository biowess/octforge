#!/usr/bin/env python3
"""OCTForge Application Launcher.

Starts single-process FastAPI backend on localhost, waits for health check,
and opens the browser frontend automatically.
"""

import sys
import time
import urllib.request
import webbrowser
import threading
import uvicorn

DEFAULT_PORT = 8000
HOST = "127.0.0.1"


def wait_for_server_and_open_browser(port: int) -> None:
    """Poll server health endpoint and open browser once operational."""
    health_url = f"http://{HOST}:{port}/api/health"
    app_url = f"http://{HOST}:{port}/"
    
    print(f"[*] Waiting for OCTForge server at {health_url}...")
    for _ in range(50):
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    print(f"[+] Server ready! Opening browser to {app_url}")
                    webbrowser.open(app_url)
                    return
        except Exception:
            time.sleep(0.1)
    
    print("[-] Warning: Timed out waiting for server health response.")


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"Invalid port specified: {sys.argv[1]}, using default {DEFAULT_PORT}")

    # Launch browser thread
    browser_thread = threading.Thread(
        target=wait_for_server_and_open_browser,
        args=(port,),
        daemon=True,
    )
    browser_thread.start()

    print(f"[*] Starting OCTForge single-process server on http://{HOST}:{port}")
    uvicorn.run("backend.app.main:app", host=HOST, port=port, reload=False)


if __name__ == "__main__":
    main()
