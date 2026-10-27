"""Run the local, offline-source checks using the active project Python."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('Use the project Python 3.12 virtual environment.')
    npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
    if not npm:
        raise SystemExit('Install Node 22 and run npm ci first.')
    for title, command in (
        ('Python unit / HTTP tests', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', '.', '-p', 'test*.py']),
        ('Existing page DOM regressions', [npm, 'run', 'test:legacy-ui']),
        ('React types / lint / tests / build', [npm, 'run', 'check:frontend']),
    ):
        print('\n[' + title + ']', flush=True)
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
