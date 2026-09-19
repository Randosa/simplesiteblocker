"""Exercise Inno file lifecycle in an isolated, non-administrator test package.

Uses a separate app ID/shortcut; Windows blocking hooks are replaced at compile
time by the packaged smoke test. Does NOT validate elevated system integration.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]


def run(command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, timeout=180,
                   creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)


def remove_test_package(uninstall, log):
    # Inno's uninstaller launches a temporary child and may return before that
    # child finishes. Wait for the completion record before inspecting/removing.
    run([uninstall, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/LOG={log}'])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if log.exists() and 'Log closed.' in log.read_text(encoding='utf-8-sig', errors='replace') and not uninstall.exists():
            return
        time.sleep(0.1)
    raise RuntimeError(f'Test uninstall did not complete; inspect {log}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', type=Path, required=True)
    args = parser.parse_args()
    shortcut = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/SSB Packaging Test.lnk'
    registry = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SSB-Packaging-Test_is1'
    if shortcut.exists():
        raise RuntimeError('An earlier packaging test remains installed; remove it first.')
    with tempfile.TemporaryDirectory(prefix='ssb-installer-test-') as directory:
        base = Path(directory)
        home = base / 'installed'
        artifacts = base / 'artifacts'
        artifacts.mkdir()
        uninstall = home / 'unins000.exe'
        try:
            for version in ('1.2.0', '1.2.1'):
                with (base / f'compile-{version}.log').open('w') as log:
                    run([args.iscc.resolve(), '/DPackagingTest', f'/DAppVersion={version}',
                         f'/O{artifacts}', ROOT / 'installer/ssb.iss'], stdout=log, stderr=subprocess.STDOUT)
                run([artifacts / f'SSB-Packaging-Test-{version}.exe', '/VERYSILENT',
                     '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART', f'/DIR={home}', f'/LOG={base / "setup.log"}'])
                assert (home / 'SSB.exe').is_file()
                assert shortcut.is_file(), 'Start menu shortcut missing'
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry) as key:
                    assert winreg.QueryValueEx(key, 'DisplayVersion')[0] == version
                if version == '1.2.0':
                    listing = {'schema': 1, 'sites': [{'hostname': 'example.org', 'include_subdomains': False}]}
                    (home / 'config/list.json').write_text(json.dumps(listing), encoding='utf-8')
                    config = json.loads((home / 'config/settings.json').read_text())
                    config.update(block_start='20:17', block_end='04:23', sync_firefox=False)
                    (home / 'config/settings.json').write_text(json.dumps(config), encoding='utf-8')
                    expected = {name: (home / 'config' / name).read_bytes() for name in ('list.json', 'settings.json')}
                else:
                    for name, contents in expected.items():
                        assert (home / 'config' / name).read_bytes() == contents, f'{name} was overwritten'
            environment = dict(os.environ)
            environment['PATH'] = os.path.join(os.environ['SystemRoot'], 'System32')
            run([home / 'SSB.exe', 'self-test'], env=environment)
            remove_test_package(uninstall, base / 'uninstall.log')
            assert not (home / 'SSB.exe').exists()
            assert not shortcut.exists()
            for name, contents in expected.items():
                assert (home / 'config' / name).read_bytes() == contents
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry):
                    raise AssertionError('Uninstall registration remains')
            except FileNotFoundError:
                pass
        finally:
            if uninstall.exists():
                remove_test_package(uninstall, base / 'cleanup.log')
    print('PASS: install, Start menu/Installed Apps registration, upgrade, exact config preservation,')
    print('packaged runtime without Python on PATH, uninstall, and retained config.')
    print('System integration was intentionally not invoked by this packaging test.')


if __name__ == '__main__':
    main()
