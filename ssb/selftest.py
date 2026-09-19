"""Non-mutating packaged-runtime smoke test (no firewall, registry or tasks)."""
import json
import os
from pathlib import Path
import tempfile
import traceback
from . import core as c
from . import settings


def run():
    report = {'version': c.APP_VERSION, 'checks': []}
    try:
        with tempfile.TemporaryDirectory(prefix='ssb-selftest-') as temporary:
            root = Path(temporary)
            c.CONFIG_PATH = root / 'config/settings.json'
            c.STATE_PATH = root / 'state/state.json'
            c.INSTALL_STATE_PATH = root / 'state/install-state.json'
            config = c.migrate_config(c.DEFAULT_CONFIG)
            settings.save_config(c.CONFIG_PATH, config)
            assert c.load_config() == config
            report['checks'].append('configuration round trip')
            config['sync_firefox'] = False
            assert all('Firefox' not in path for path, _ in c.browser_policy_values(config))
            report['checks'].append('Firefox opt out')
            from .ui import SSBWindow
            window = SSBWindow()
            window.root.withdraw()
            window.root.update()
            assert window.logo.width() > 0
            window.root.destroy()
            report['checks'].append('Tk runtime, window, and icons')
            from .updater import Updater
            updater = Updater()
            updater.close()
            report['checks'].append('WinSparkle runtime and public key')
        report['success'] = True
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    (Path(tempfile.gettempdir()) / 'SSB-selftest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['success'] else 1
