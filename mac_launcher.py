"""Embedded service entry point for the native Swift/WKWebView Mac window."""
import signal
import sys
import threading
import os


def main():
    from web_dialogue.updater_cli import dispatch
    if dispatch(sys.argv[1:]): return
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        from web_dialogue.worker import main as worker_main
        worker_main(sys.argv[2])
        return
    from pathlib import Path
    from web_dialogue.install_lock import InstallationLease
    installation_lease=InstallationLease(Path(sys.executable).parents[2]).acquire() if getattr(sys,"frozen",False) else None
    from web_dialogue.app import make_server
    from web_dialogue.verification_profile import verification_root
    state_dir = verification_root() or Path.home() / 'Library' / 'Application Support' / 'DialogueTTS'
    native_token = os.environ.pop('DIALOGUE_NATIVE_TOKEN', None)
    native_state = os.environ.pop('DIALOGUE_NATIVE_STATE_DIR', None)
    if native_token and native_state and verification_root() is None:
        if not Path(native_state).is_absolute(): raise ValueError('原生状态目录必须为绝对路径')
        state_dir = Path(native_state)
    server = make_server(0, bind='127.0.0.1', state_dir=state_dir if native_token else None, native_token=native_token)
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
        if getattr(server, 'workspaces', None): server.workspaces.close()
        server.jobs.close()
        if installation_lease: installation_lease.close()


if __name__ == '__main__':
    main()
