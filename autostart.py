"""Запуск вместе с OBS и единственная копия программы для этой системы. Только стандартная библиотека."""
import sys

if sys.platform == "win32":
    import win32autostart as _native
else:
    import linuxautostart as _native
sys.modules[__name__] = _native
