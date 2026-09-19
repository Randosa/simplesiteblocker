"""Explicitly authorized real Windows installer/integration test."""
import ctypes
import datetime as dt
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import winreg
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1]
OUT = SOURCE / 'build/admin-test'
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(SOURCE))
from ssb import core as c, settings

REPORT = {'started': dt.datetime.now().astimezone().isoformat(), 'checks': [], 'success': False}
SETUP = SOURCE / f'release/SSB-Setup-{c.APP_VERSION}.exe'
HOME = Path(r'C:\Program Files\SSB')
EXE = HOME / 'SSB.exe'
POLICIES = [r'SOFTWARE\Policies\Google\Chrome\URLBlocklist',
            r'SOFTWARE\Policies\Microsoft\Edge\URLBlocklist', c.FIREFOX_BLOCK_PATH]


class QuietHTTP(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def record(message):
    REPORT['stage'] = message
    settings.atomic_json(OUT / 'report.json', REPORT)
    with (OUT / 'progress.log').open('a', encoding='utf-8') as handle:
        handle.write(dt.datetime.now().isoformat() + ' ' + message + '\n')


def passed(message):
    REPORT['checks'].append(message)
    record(message)


def run(args, timeout=180):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1:]} exited {result.returncode}: {result.stderr or result.stdout}')
    return result


def registry(path):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            values = {}
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, index)
                values[name] = [value, kind]
            return values
    except FileNotFoundError:
        return {}


def install(label):
    record(label)
    run([SETUP, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART', f'/LOG={OUT / (label + ".log")}'])
    assert EXE.is_file()
    assert c.installation_complete()


def verify_policy(enabled, config):
    install_state = c.load_json(c.INSTALL_STATE_PATH)
    owned = [entry for entry in install_state['browser_block_policies'] if entry['path'] == c.FIREFOX_BLOCK_PATH]
    if enabled:
        assert owned, 'Firefox ownership records missing'
        actual = registry(c.FIREFOX_BLOCK_PATH)
        for entry in owned:
            assert actual.get(entry['name'], [None])[0] == entry['value']
    else:
        assert registry(c.FIREFOX_BLOCK_PATH) == REPORT['baseline']['policies'][c.FIREFOX_BLOCK_PATH]


def updater_handoff():
    record('WinSparkle signed download and installer handoff')
    serve = OUT / 'feed'
    serve.mkdir(exist_ok=True)
    shutil.copy2(SETUP, serve / SETUP.name)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHTTP, directory=str(serve)))
    tree = ET.parse(SOURCE / 'release/appcast.xml')
    enclosure = tree.find('channel/item/enclosure')
    enclosure.set('url', f'http://127.0.0.1:{server.server_port}/{SETUP.name}')
    log = OUT / 'winsparkle-install.log'
    enclosure.set('{http://www.andymatuschak.org/xml-namespaces/sparkle}installerArguments',
                  f'/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /LOG="{log}"')
    tree.write(serve / 'appcast.xml', encoding='utf-8', xml_declaration=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    child = OUT / 'updater_host.py'
    child.write_text('''import ctypes,sys,time,threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ssb import updater
updater.APP_VERSION = '1.1.9'
updater.FEED = sys.argv[2]
updater.resource = lambda name: Path(r'C:\\Program Files\\SSB\\_internal') / name
instance = updater.Updater()
error = threading.Event()
callback_type = ctypes.CFUNCTYPE(None)
callback = callback_type(error.set)
instance._callbacks.append(callback)
instance._bind('set_error_callback', [callback_type])(callback)
instance._bind('check_update_with_ui_and_install', [])()
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    if error.is_set():
        Path(sys.argv[3]).write_text('error')
        instance.close()
        sys.exit(1)
    if instance.shutdown_requested.is_set():
        Path(sys.argv[3]).write_text('installer launched; shutdown requested')
        sys.exit(0)
    time.sleep(.1)
instance.close()
raise RuntimeError('Updater timed out')
''', encoding='utf-8')
    try:
        run([sys.executable, child, SOURCE, f'http://127.0.0.1:{server.server_port}/appcast.xml', OUT / 'handoff.txt'], timeout=200)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if log.exists() and 'Log closed.' in log.read_text(encoding='utf-8-sig', errors='replace'):
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Updater installer did not finish')
        assert c.installation_complete()
        assert not (HOME / 'state/maintenance.json').exists()
        assert 'Installation process succeeded.' in log.read_text(encoding='utf-8-sig')
    finally:
        server.shutdown()
        server.server_close()
    passed('Actual WinSparkle signed download, installer launch, shutdown callback, and completed installation')


def main():
    assert ctypes.windll.shell32.IsUserAnAdmin(), 'Administrator approval required'
    assert not EXE.exists(), 'Expected a fresh test machine; refusing to overwrite an existing install'
    assert not Path(r'C:\ProgramData\CodexSiteBlocker\install-state.json').exists()
    REPORT['baseline'] = {'policies': {path: registry(path) for path in POLICIES},
                          'defender': c.defender_network_protection(), 'firewall_count': c.firewall_rule_count()}
    assert REPORT['baseline']['firewall_count'] == 0, 'Existing SSB firewall rules require investigation first'
    assert REPORT['baseline']['defender'] in (0, 1, 2), 'Cannot read the prior Defender setting safely'
    record('Baseline recorded; beginning elevated installation')
    install('fresh-install')
    defaults = c.load_config()
    settings.atomic_json(OUT / 'initial-config.json', defaults)
    shortcut = Path(os.environ['ProgramData']) / 'Microsoft/Windows/Start Menu/Programs/SSB — Simple Site Blocker.lnk'
    assert shortcut.exists()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{504BBC0B-9861-49D0-A993-BD5D9AE73455}_is1') as key:
        assert winreg.QueryValueEx(key, 'DisplayVersion')[0] == c.APP_VERSION
    task = c.run(['schtasks.exe', '/Query', '/TN', c.TASK_NAME, '/XML']).stdout
    (OUT / 'installed-task.xml').write_text(task, encoding='utf-8')
    assert str(EXE) in task and '<Arguments>enforce</Arguments>' in task
    assert 'S-1-5-18' in task
    run([EXE, 'self-test'])
    passed('Fresh Program Files installation, Start shortcut, Installed Apps, SYSTEM executable task, and packaged smoke test')
    test = {**defaults, 'sites': [{'hostname': 'example.com', 'include_subdomains': False}],
            'block_start': '00:00', 'block_end': '00:00', 'sync_firefox': True}
    c.save_json(c.CONFIG_PATH, test)
    run([EXE, 'repair'])
    assert c.load_json(c.STATE_PATH)['blocked']
    assert c.firewall_rule_count() == 1
    verify_policy(True, test)
    passed('Active schedule creates actual firewall rule and Firefox block entries')
    test['sync_firefox'] = False
    c.save_json(c.CONFIG_PATH, test)
    run([EXE, 'repair'])
    verify_policy(False, test)
    assert c.firewall_rule_count() == 1
    passed('Firefox opt-out removes owned Firefox entries while keeping firewall blocking')
    now = dt.datetime.now()
    test.update(sync_firefox=True, block_start=(now+dt.timedelta(hours=2)).strftime('%H:%M'),
                block_end=(now+dt.timedelta(hours=3)).strftime('%H:%M'))
    c.save_json(c.CONFIG_PATH, test)
    run([EXE, 'repair'])
    assert not c.load_json(c.STATE_PATH)['blocked']
    assert c.firewall_rule_count() == 0
    for path in POLICIES:
        assert registry(path) == REPORT['baseline']['policies'][path]
    passed('Outside blocking hours clears actual rules and restores pre-existing browser policy values')
    c.powershell("Start-ScheduledTask -TaskPath '\\Simple Site Blocker\\' -TaskName 'Reconcile'; Start-Sleep -Seconds 12; $i=Get-ScheduledTaskInfo -TaskPath '\\Simple Site Blocker\\' -TaskName 'Reconcile'; if($i.LastTaskResult -ne 0){throw ('Task result '+$i.LastTaskResult)}")
    passed('Real scheduled task completes as SYSTEM with exit code zero')
    saved = {name: (HOME / 'config' / name).read_bytes() for name in ('settings.json','list.json')}
    updater_handoff()
    for name, data in saved.items():
        # Applying validated config can normalize whitespace; compare semantic data.
        assert json.loads((HOME / 'config' / name).read_bytes()) == json.loads(data)
    passed('WinSparkle upgrade preserves website list, schedule, and Firefox preference')
    record('Testing actual uninstaller and restoration')
    uninstall = HOME / 'unins000.exe'
    uninstall_log = OUT / 'uninstall.log'
    run([uninstall, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/LOG={uninstall_log}'])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if not uninstall.exists() and uninstall_log.exists() and 'Log closed.' in uninstall_log.read_text(encoding='utf-8-sig', errors='replace'):
            break
        time.sleep(.2)
    assert not EXE.exists()
    assert not shortcut.exists()
    assert c.firewall_rule_count() == 0
    assert c.defender_network_protection() == REPORT['baseline']['defender']
    for path in POLICIES:
        assert registry(path) == REPORT['baseline']['policies'][path]
    assert c.run(['schtasks.exe', '/Query', '/TN', c.TASK_NAME], check=False).returncode != 0
    for name, data in saved.items():
        assert json.loads((HOME / 'config' / name).read_bytes()) == json.loads(data)
    passed('Actual uninstall removes executable, shortcut, task and rules; restores browser/Defender values; retains configuration')
    c.save_json(c.CONFIG_PATH, defaults)
    install('final-reinstall')
    assert c.load_config() == defaults
    passed('Final reinstall restores original default website list and schedule')
    REPORT['success'] = True
    record(f'Administrator test passed; SSB {c.APP_VERSION} is installed with its original defaults')


try:
    main()
except Exception:
    REPORT['error'] = traceback.format_exc()
    record('TEST FAILED — inspect report and installer logs before publishing')
    sys.exit(1)
