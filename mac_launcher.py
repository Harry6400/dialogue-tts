"""Embedded service entry point for the native Swift/WKWebView Mac window."""
import signal
import sys
import threading


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        from web_dialogue.worker import main as worker_main
        worker_main(sys.argv[2])
        return
    from web_dialogue.app import make_server
    server = make_server(0, bind='127.0.0.1')
    server.can_shutdown = True

    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print('http://127.0.0.1:' + str(server.server_address[1]), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.jobs.close()


if __name__ == '__main__':
    main()
