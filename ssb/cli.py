"""Packaged and development entry points."""
import argparse
import ctypes
import json
import logging
import sys
from . import core as c
from .lifecycle import operation_lock, prepare_update, finish_update


def main(argv=None):
    parser = argparse.ArgumentParser(description='SSB — Simple Site Blocker')
    parser.add_argument('--gui', action='store_true')
    sub = parser.add_subparsers(dest='command')
    for name in ('enforce', 'status', 'repair', 'uninstall', 'instructions',
                 'prepare-update', 'finish-update', 'self-test'):
        sub.add_parser(name)
    unlock = sub.add_parser('unlock')
    unlock.add_argument('--minutes', type=int, default=30)
    unlock.add_argument('--reason', default='Temporary exception')
    args = parser.parse_args(argv)
    try:
        if args.command == 'self-test':
            from .selftest import run
            return run()
        if args.command == 'instructions':
            print(c.HELP_TEXT)
            return 0
        if args.command is None:
            if not c.is_admin():
                c.relaunch_elevated_gui()
                return 0
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Randosa.SSB')
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            kernel.CreateMutexW.restype = ctypes.c_void_p
            mutex = kernel.CreateMutexW(None, False, 'Global\\SSB.Manager')
            if not mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.get_last_error() == 183:
                raise RuntimeError('SSB is already open. Close its other window first.')
            try:
                c.run_gui()
            finally:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle(mutex)
            return 0
        c.require_windows_admin()
        c.configure_logging()
        if args.command == 'repair':
            c.install_or_update(c.load_config())
        elif args.command == 'uninstall':
            c.uninstall()
        elif args.command == 'prepare-update':
            prepare_update()
        elif args.command == 'finish-update':
            finish_update()
        else:
            with operation_lock():
                if (c.INSTALL_STATE_PATH.parent / 'maintenance.json').exists():
                    return 0
                if args.command == 'enforce':
                    # A queued task may outlive task deletion during uninstall.
                    if c.installation_complete():
                        c.enforce()
                elif args.command == 'unlock':
                    c.unlock(args.minutes, args.reason)
                elif args.command == 'status':
                    print(json.dumps(c.status_payload(), indent=2))
        return 0
    except Exception as exc:
        logging.exception('SSB operation failed')
        if args.command is None:
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'SSB could not start', 0x10)
        elif sys.stderr is not None:
            print(str(exc), file=sys.stderr)
        return 1
