"""Starts Boot Tool from the downloaded app (no Python or Terminal needed on the user's side)."""
import sys
import threading
import webbrowser

from boottool.ui import make_server


def main() -> None:
    try:
        srv = make_server("127.0.0.1", 8765)
    except OSError:                                    # 8765 is busy (Boot Tool may already be open): take any free port
        srv = make_server("127.0.0.1", 0)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print("=" * 56)
    print("  BOOT TOOL is running.")
    print(f"  It should open in your browser: {url}")
    print("  Keep this window open while you work.")
    print("  Close this window (or press Ctrl+C) to quit.")
    print("=" * 56, flush=True)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
