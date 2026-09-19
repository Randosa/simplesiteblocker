"""Minimal typed ctypes binding to the pinned WinSparkle runtime."""
import base64
import ctypes
import threading
from .core import APP_VERSION
from .paths import resource

FEED = 'https://raw.githubusercontent.com/Randosa/simplesiteblocker/main/updates/appcast.xml'


class Updater:
    def __init__(self, can_shutdown=lambda: True):
        self.shutdown_requested = threading.Event()
        self.dll = ctypes.CDLL(str(resource('vendor/WinSparkle.dll')), winmode=0x1100)
        self._callbacks = []
        self.started = False
        key = resource('updates/public-key.txt').read_text(encoding='ascii').strip()
        if len(base64.b64decode(key, validate=True)) != 32:
            raise ValueError('The update verification key is invalid. Reinstall SSB from its official release.')
        self._bind('set_app_details', [ctypes.c_wchar_p] * 3)('Randosa', 'SSB', APP_VERSION)
        self._bind('set_appcast_url', [ctypes.c_char_p])(FEED.encode('ascii'))
        if self._bind('set_eddsa_public_key', [ctypes.c_char_p], ctypes.c_int)(key.encode('ascii')) != 1:
            raise RuntimeError('WinSparkle rejected the update verification key.')
        self._bind('set_automatic_check_for_updates', [ctypes.c_int])(0)
        allowed_type = ctypes.CFUNCTYPE(ctypes.c_int)
        shutdown_type = ctypes.CFUNCTYPE(None)
        allowed = allowed_type(lambda: int(can_shutdown()))
        shutdown = shutdown_type(self.shutdown_requested.set)
        self._callbacks.extend([allowed, shutdown])
        self._bind('set_can_shutdown_callback', [allowed_type])(allowed)
        self._bind('set_shutdown_request_callback', [shutdown_type])(shutdown)
        self._bind('init', [])()
        self.started = True

    def _bind(self, suffix, args, result=None):
        function = getattr(self.dll, 'win_sparkle_' + suffix)
        function.argtypes = args
        function.restype = result
        return function

    def check(self):
        self._bind('check_update_with_ui', [])()

    def close(self):
        if self.started:
            self._bind('cleanup', [])()
            self.started = False
