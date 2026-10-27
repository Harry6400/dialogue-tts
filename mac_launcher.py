"""Entry point for a self-contained, manually launched macOS app bundle."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import webbrowser


def prepare_bundled_tools():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    bundled_bin = base / "bin"
    if bundled_bin.is_dir():
        os.environ["PATH"] = str(bundled_bin) + os.pathsep + os.environ.get("PATH", "")


def main():
    prepare_bundled_tools()
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        from web_dialogue.worker import main as worker_main
        worker_main(sys.argv[2])
        return
    from web_dialogue.app import make_server
    server = make_server(0)
    server.can_shutdown = True
    def close_on_signal(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, close_on_signal)
    signal.signal(signal.SIGINT, close_on_signal)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Timer(0.6, lambda: webbrowser.open(url, new=1)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.jobs.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if sys.platform == "darwin" and not (len(sys.argv) > 1 and sys.argv[1] == "--worker"):
            subprocess.run(["/usr/bin/osascript", "-e", 'display alert "对话配音未能启动" message "请确认应用文件完整，或联系提供者检查构建。没有修改输入文件。" as critical'], check=False)
        raise
