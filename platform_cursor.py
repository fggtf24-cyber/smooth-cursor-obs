"""Курсор, мониторы и окна для этой системы: win32cursor или linuxcursor под одним именем."""
import sys

if sys.platform == "win32":
    import win32cursor as _native
else:
    import linuxcursor as _native
sys.modules[__name__] = _native
