"""修复 DSH 沙箱下 tempfile.mkdtemp 用 0o700 建目录导致不可写的问题，从而让 pip 可用。"""
import os as _os

_real_mkdir = _os.mkdir


def _mkdir(path, mode=0o777, *args, **kwargs):
    if mode == 0o700:
        mode = 0o777
    return _real_mkdir(path, mode, *args, **kwargs)


_os.mkdir = _mkdir