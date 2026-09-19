"""Build the standalone Windows app; run from the repository root."""
import argparse
import json
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssb.core import APP_VERSION, DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--winsparkle', type=Path, required=True, help='Extracted official WinSparkle 0.9.4 directory')
    parser.add_argument('--iscc', type=Path)
    args = parser.parse_args()
    from PIL import Image, ImageDraw
    icons = ROOT / 'icons'
    # Compile Windows icon sizes from the supplied artwork; preserve source PNGs.
    original = Image.open(icons / 'ssbicon-bkg.png').convert('RGBA')
    original = original.resize((1024, 1024), Image.Resampling.LANCZOS)
    mask = Image.new('L', (1024, 1024))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 1023, 1023), radius=190, fill=255)
    original.putalpha(mask)
    original.save(icons / 'ssb.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    original.resize((256, 256), Image.Resampling.LANCZOS).save(icons / 'app-preview.png')
    header = Image.open(icons / 'ssbicon-transparent.png').convert('RGBA')
    header.thumbnail((64, 64), Image.Resampling.LANCZOS)
    header.save(icons / 'header.png')
    vendor = ROOT / 'vendor'
    vendor.mkdir(exist_ok=True)
    shutil.copy2(args.winsparkle / 'x64/Release/WinSparkle.dll', vendor / 'WinSparkle.dll')
    for name in ('COPYING', 'COPYING.expat', 'AUTHORS'):
        shutil.copy2(args.winsparkle / name, vendor / name)
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', vendor / 'PYTHON-LICENSE.txt')
    shutil.copy2(Path(sys.base_prefix) / 'tcl/tk8.6/license.terms', vendor / 'TK-LICENSE.txt')
    distribution = importlib.metadata.distribution('pyinstaller')
    bootloader_license = next(path for path in distribution.files if str(path).endswith('/licenses/COPYING.txt'))
    shutil.copy2(distribution.locate_file(bootloader_license), vendor / 'PYINSTALLER-LICENSE.txt')
    defaults = ROOT / 'defaults'
    defaults.mkdir(exist_ok=True)
    (defaults / 'settings.json').write_text(json.dumps({k: v for k,v in DEFAULT_CONFIG.items() if k != 'sites'}, indent=2), encoding='utf-8')
    (defaults / 'list.json').write_text(json.dumps({'schema': 1, 'sites': DEFAULT_CONFIG['sites']}, indent=2), encoding='utf-8')
    if not (ROOT / 'updates/public-key.txt').is_file():
        raise SystemExit('Create the update signing key before building; never ship a placeholder key.')
    version = tuple(map(int, APP_VERSION.split('.'))) + (0,)
    info = f'''VSVersionInfo(ffi=FixedFileInfo(filevers={version}, prodvers={version}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName','Randosa'), StringStruct('FileDescription','SSB — Simple Site Blocker'), StringStruct('FileVersion','{APP_VERSION}'), StringStruct('ProductName','SSB'), StringStruct('ProductVersion','{APP_VERSION}'), StringStruct('OriginalFilename','SSB.exe')])]), VarFileInfo([VarStruct('Translation',[1033,1200])])])'''
    (ROOT / 'version-info.txt').write_text(info, encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                    '--windowed', '--name', 'SSB', '--icon', str(icons / 'ssb.ico'),
                    '--version-file', 'version-info.txt', '--add-data', 'icons;icons',
                    '--add-data', 'updates/public-key.txt;updates', '--add-data', 'vendor;vendor',
                    'site_blocker.py'], cwd=ROOT, check=True)
    if args.iscc:
        subprocess.run([str(args.iscc.resolve()), f'/DAppVersion={APP_VERSION}',
                        str(ROOT / 'installer/ssb.iss')], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
