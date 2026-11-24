"""One cooperative native test/cook at a time on this Windows login session.

The supervising Python thread holds the kernel mutex through child cleanup.
No PID, state file, stale timestamp or executable name establishes ownership.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os

NAME = r'Local\Tribes3ResearchNativeResourcesV1'
_failed_handles = []


class NativeResourceBusy(RuntimeError):
    pass


@contextmanager
def native_resources(*, name=NAME, timeout_ms=0):
    if os.name != 'nt':
        raise OSError('The shared native resource gate requires Windows.')
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 60000:
        raise ValueError('Wait must be between zero and 60000 milliseconds.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE,wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None,False,name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle,timeout_ms)
        if result == 0x102:
            raise NativeResourceBusy('Another cooperating TRIBES test or cook holds the native resource gate.')
        if result not in (0,0x80):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield {'abandoned_owner':result == 0x80}
    finally:
        if acquired and not kernel.ReleaseMutex(handle):
            _failed_handles.append(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.CloseHandle(handle):
            _failed_handles.append(handle)
            raise ctypes.WinError(ctypes.get_last_error())


def run_exclusive(main):
    try:
        with native_resources():
            return main()
    except NativeResourceBusy as error:
        print(str(error),flush=True)
        return 75
