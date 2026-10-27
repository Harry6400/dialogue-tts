"""Explicit OS-owned verification profile; never accepts a webpage path."""
import os
from pathlib import Path
import stat

ENV = 'DIALOGUE_VERIFICATION_STATE_ROOT'

def verification_root():
    value = os.environ.get(ENV)
    if not value:
        return None
    root = Path(value)
    info = root.lstat()
    if not root.is_absolute() or not stat.S_ISDIR(info.st_mode) or root.is_symlink():
        raise ValueError('Invalid verification profile')
    if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('Verification profile must be private and owned by this user')
    return root.resolve(strict=True)
