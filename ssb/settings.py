"""Validated JSON storage with recoverable two-file configuration commits."""
from contextlib import contextmanager
import json
import msvcrt
import os
from pathlib import Path
import tempfile
import time


@contextmanager
def file_lock(path, timeout=30):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another SSB operation is still running. Try again shortly.')
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open(encoding='utf-8-sig') as handle:
            return json.load(handle)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{path.name} is damaged. Restore its backup or repair the configuration.') from exc


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _recover(path):
    journal = path.parent / '.config-transaction.json'
    data = read_json(journal)
    if data is not None:
        # A journal is durable before either destination changes. Finish that commit.
        if not isinstance(data, dict) or set(data) != {'settings', 'list'}:
            raise ValueError('Configuration recovery data is damaged.')
        atomic_json(path.parent / 'list.json', data['list'])
        atomic_json(path, data['settings'])
        journal.unlink()


def _load(path, default):
    listing = path.parent / 'list.json'
    if not path.exists() and not listing.exists():
        return default
    if not path.exists() or not listing.exists():
        raise ValueError('The configuration is incomplete: settings.json and list.json are both required.')
    config, sites = read_json(path), read_json(listing)
    if not isinstance(config, dict) or not isinstance(sites, dict) or sites.get('schema') != 1 or not isinstance(sites.get('sites'), list):
        raise ValueError('The SSB configuration format is not recognized.')
    return {**config, 'sites': sites['sites']}


def load_config(path, default=None):
    path = Path(path)
    # Read-only empty detection permits the initial UI to display defaults.
    if not path.parent.exists():
        return default
    with file_lock(path.parent / '.config.lock'):
        _recover(path)
        return _load(path, default)


def save_config(path, config):
    path = Path(path)
    if not isinstance(config, dict) or not isinstance(config.get('sites'), list):
        raise ValueError('A website list is required.')
    with file_lock(path.parent / '.config.lock'):
        _recover(path)
        old = _load(path, None)
        if old is not None:
            atomic_json(path.parent / 'backup.json', old)
        values = {k: v for k, v in config.items() if k != 'sites'}
        atomic_json(path.parent / '.config-transaction.json',
                    {'settings': values, 'list': {'schema': 1, 'sites': config['sites']}})
        _recover(path)
