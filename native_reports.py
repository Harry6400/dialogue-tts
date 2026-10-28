"""Native-selected CSV destinations, separate from MP3 grants and receipts.

Only the native save-panel handler may construct a destination. Browser payloads
must never be passed to this module through an ordinary application endpoint.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import uuid


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('报告目标目录不可用，请重新选择。')
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class NativeReportDestination:
    """An exact file selected by the native save panel, never a directory grant."""
    _path: Path
    _parent_identity: tuple

    @classmethod
    def from_native_selection(cls, path):
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError('请通过系统面板选择 CSV 保存位置。')
        target = Path(path)
        if not target.is_absolute() or '..' in target.parts:
            raise ValueError('报告目标路径无效。')
        name = target.name
        if not name or name in {'.', '..'} or '\\' in name or '\x00' in name or target.suffix.lower() != '.csv' or len(os.fsencode(name)) > 255:
            raise ValueError('请选择新的 .csv 文件。')
        # Resolve native/system aliases once (for example macOS /var), but never
        # accept a linked final parent or an existing final file.
        identity = _directory(target.parent)
        parent = target.parent.resolve(strict=True)
        if _directory(parent) != identity:
            raise ValueError('报告目标目录已变化，请重新选择。')
        target = parent / name
        if os.path.lexists(target):
            raise FileExistsError('同名文件已存在，请选择新的 CSV 文件名。')
        return cls(target, identity)

    def _check(self):
        if _directory(self._path.parent) != self._parent_identity:
            raise ValueError('报告目标目录已变化，请重新选择。')
        if os.path.lexists(self._path):
            raise FileExistsError('同名文件已存在，未覆盖任何文件。')


def save_report_to_destination(handle, manifest, destination):
    """Stream one leased report into an exact native-selected new CSV file.

    A failed publication never removes an existing destination. If a filesystem
    error happens after publication, the published file is retained and the
    caller keeps the report snapshot for a deliberate retry.
    """
    if not isinstance(destination, NativeReportDestination):
        raise ValueError('报告保存需要原生系统面板授权。')
    expected_size, expected_hash = manifest['size'], manifest['sha256']
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError('报告长度无效。')
    destination._check()
    parent, target = destination._path.parent, destination._path
    directory_fd = None
    if os.name != 'nt':
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0))
        info = os.fstat(directory_fd)
        if (info.st_dev, info.st_ino) != destination._parent_identity:
            os.close(directory_fd)
            raise ValueError('报告目标目录已变化，请重新选择。')
    temporary = '.dialogue-report-' + uuid.uuid4().hex + '.tmp'
    staging = parent / temporary
    created_identity = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd) if directory_fd is not None else os.open(staging, flags, 0o600)
        created = os.fstat(fd)
        created_identity = (created.st_dev, created.st_ino)
        size, digest = 0, hashlib.sha256()
        with os.fdopen(fd, 'wb') as output:
            handle.seek(0)
            while True:
                block = handle.read(65536)
                if not block:
                    break
                size += len(block)
                if size > expected_size:
                    raise ValueError('报告内容已变化，未发布文件。')
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
            staged_identity = os.fstat(output.fileno())
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError('报告内容校验失败，未发布文件。')
        destination._check()
        current = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False) if directory_fd is not None else staging.lstat()
        if not _regular(current) or (current.st_dev, current.st_ino) != created_identity:
            raise ValueError('临时报告身份已变化。')
        if os.name == 'nt':
            os.rename(staging, target)  # Windows rename does not replace.
        else:
            os.link(temporary, target.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
        if directory_fd is not None:
            os.fsync(directory_fd)
        # Verify the real publication, not merely the source file or copy call.
        before = target.lstat()
        if not _regular(before) or (before.st_dev, before.st_ino) != (staged_identity.st_dev, staged_identity.st_ino):
            raise ValueError('报告已发布，但无法确认目标文件身份，请核对所选目录。')
        check_fd = os.open(target.name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=directory_fd) if directory_fd is not None else os.open(target, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        with os.fdopen(check_fd, 'rb') as check:
            actual = os.fstat(check.fileno())
            if not _regular(actual) or (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('报告已发布，但目标文件发生变化，请核对。')
            actual_hash = hashlib.sha256()
            actual_size = 0
            while True:
                block = check.read(65536)
                if not block:
                    break
                actual_size += len(block)
                if actual_size > expected_size:
                    raise ValueError('报告已发布，但目标文件发生变化，请核对。')
                actual_hash.update(block)
        if actual_size != expected_size or actual_hash.hexdigest() != expected_hash or _directory(parent) != destination._parent_identity:
            raise ValueError('报告已发布，但保存后核验失败，请核对所选目录。')
        return {'ok': True, 'save_id': uuid.uuid4().hex, 'report_id': manifest['report_id'],
                'name': target.name, 'path': str(target), 'size': size, 'sha256': expected_hash}
    finally:
        try:
            if created_identity is not None:
                try:
                    current = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False) if directory_fd is not None else staging.lstat()
                    owned = _regular(current) and (current.st_dev, current.st_ino) == created_identity
                except FileNotFoundError:
                    owned = False
                if owned and directory_fd is not None:
                    os.unlink(temporary, dir_fd=directory_fd)
                elif owned:
                    staging.unlink()
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

