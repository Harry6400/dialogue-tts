"""Per-installation single-instance lock; survives in state, not in app bundle.

v0.3 and later hosts hold this for their entire backend lifetime. The update
helper obtains it after old GUI/service exit. No process scanning/termination.
"""
import hashlib, os, sys
from pathlib import Path
from .safe_updater import UpdateError

class InstallationLease:
    def __init__(self, target):
        from .updater_cli import state_directory
        root=state_directory()/'installation-locks';root.mkdir(mode=0o700,parents=True,exist_ok=True)
        key=hashlib.sha256(os.path.normcase(str(Path(target).resolve())).encode()).hexdigest()
        self.path=root/(key+'.lock');self.handle=None
    def acquire(self):
        f=open(self.path,'a+b')
        if f.seek(0,2)==0: f.write(b'0');f.flush()
        f.seek(0)
        try:
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,IOError):f.close();raise UpdateError('此应用已有实例运行，请先正常关闭其它窗口后重试')
        self.handle=f;return self
    def close(self):
        if self.handle:
            self.handle.close();self.handle=None
    def __enter__(self):return self.acquire()
    def __exit__(self,*_):self.close()
