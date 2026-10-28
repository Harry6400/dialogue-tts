"""Reproducible source selection shared by both platform builds."""
from pathlib import Path
import zipfile

ROOT_FILES = ('AGENTS.md', 'PRODUCT.md', 'DESIGN.md', 'LICENSE', 'NOTICE', 'README.md',
              'README_构建与许可.md', 'WINDOWS_开始使用.txt', '.gitignore',
              '.python-version', '.node-version', 'package.json', 'package-lock.json')
ROOT_DIRS = ('docs', 'web_dialogue', 'native_macos', 'frontend', 'examples', 'assets',
             'third_party', 'tools', '.github/workflows')
EXCLUDED = {'node_modules', 'dist', 'build', '__pycache__', '.venv', '.cache', 'coverage'}

def source_files(root):
    root = Path(root)
    candidates = {root / name for name in ROOT_FILES}
    candidates.update(root.glob('*.py'))
    candidates.update(root.glob('requirements*.txt'))
    candidates.update(root.glob('requirements*.lock'))
    for name in ROOT_DIRS:
        candidates.update((root / name).rglob('*'))
    return sorted(p for p in candidates if p.is_file() and not p.is_symlink()
                  and not EXCLUDED.intersection(p.relative_to(root).parts)
                  and p.suffix not in {'.pyc', '.mp3'} and p.name != '.DS_Store') + [root / 'web_dialogue/silence.mp3']

def export_sources(root, destination):
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files(root):
            archive.write(path, 'DialogueTTS-Source/' + path.relative_to(root).as_posix())
