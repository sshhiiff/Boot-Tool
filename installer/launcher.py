"""Starts Boot Tool from the downloaded app (no Python or Terminal needed on the user's side).

Started with --no-browser it does not open the page in the browser (for trying the app out by
program: see smoke_test.py)."""
import sys
import threading
import webbrowser

from boottool.ui import make_server, stop_on_close


def main() -> None:
    try:
        srv = make_server("127.0.0.1", 8765)
    except OSError:                                    # 8765 is busy (Boot Tool may already be open): take any free port
        srv = make_server("127.0.0.1", 0)
    stop_on_close()                                    # closing the Terminal window still tidies up (Mac)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    browser = "--no-browser" not in sys.argv[1:]
    print("=" * 56)
    print("  BOOT TOOL is running.")
    print(f"  It should open in your browser: {url}" if browser else f"  Open it in your browser: {url}")
    print("  Keep this window open while you work.")
    print("  Close this window (or press Ctrl+C) to quit.")
    print("=" * 56, flush=True)
    if browser:
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
