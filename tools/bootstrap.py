"""Fetch hash-pinned official Windows build tools into .tools."""
import hashlib
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1] / '.tools'
ASSETS = {
    'WinSparkle-0.9.4.zip': (
        'https://github.com/vslavik/winsparkle/releases/download/v0.9.4/WinSparkle-0.9.4.zip',
        '6037df37fc263bd1650a1c4949681a9d40ffe991d01f35892a406cb5d103c976'),
    'innosetup.exe': (
        'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe',
        '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'),
}


def main():
    ROOT.mkdir(exist_ok=True)
    for name, (url, digest) in ASSETS.items():
        target = ROOT / name
        if not target.exists():
            with urllib.request.urlopen(url, timeout=120) as response:
                target.write_bytes(response.read())
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'{name}: checksum mismatch; do not execute this download.')
    with zipfile.ZipFile(ROOT / 'WinSparkle-0.9.4.zip') as archive:
        archive.extractall(ROOT / 'winsparkle')
    subprocess.run([str(ROOT / 'innosetup.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                    '/NORESTART', '/CURRENTUSER', '/NOICONS', f'/DIR={ROOT / "InnoSetup"}'],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    print('Verified WinSparkle 0.9.4 and installed Inno Setup 6.7.3 in .tools.')


if __name__ == '__main__':
    main()
