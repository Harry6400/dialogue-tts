"""Store Unicode paths, POSIX modes and in-bundle symlinks without dereferencing."""
from pathlib import Path
import os
import stat
import zipfile

def package_macos(stage, destination):
    stage = Path(stage)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(stage.rglob('*')):
            if path.name == '.DS_Store' or path.name.startswith('._'):
                continue
            name = path.relative_to(stage.parent).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if stat.S_ISLNK(mode):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, os.readlink(path).encode('utf-8'))
            elif stat.S_ISREG(mode):
                archive.write(path, name)
            else:
                raise ValueError('Unsupported distribution entry')
