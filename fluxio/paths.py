"""Rutas de la aplicación y carga del yt-dlp actualizado.

Este módulo NO importa yt_dlp: se usa antes de importarlo para que, dentro del .exe,
se cargue la versión descargada con «Actualizar yt-dlp» si es más reciente.
"""
import os
import re
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)   # True dentro del .exe (PyInstaller)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) if FROZEN else APP_DIR
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"


def fix_std_streams():
    """Sin consola (--windowed) stdout/stderr valen None: evita fallos al escribir en ellos."""
    if FROZEN:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def ytdlp_update_dirs():
    """Carpetas donde se guarda el yt-dlp descargado con «Actualizar»: junto al .exe
    (portable) y, si ahí no se puede escribir, en %APPDATA%."""
    dirs = [APP_DIR / "yt-dlp_actualizado"]
    if os.environ.get("APPDATA"):
        dirs.append(Path(os.environ["APPDATA"]) / "Fluxio" / "yt-dlp_actualizado")
    return dirs


def load_ytdlp_override():
    """En el .exe: si hay un yt-dlp descargado más reciente que el incluido, se usa ese."""
    if not FROZEN:
        return
    try:
        from importlib.metadata import version
        bundled = version_tuple(version("yt-dlp"))
    except Exception:
        bundled = ()
    for d in ytdlp_update_dirs():
        try:
            v = version_tuple((d / "version.txt").read_text())
        except Exception:
            continue
        if (d / "yt-dlp").is_file() and v > bundled:
            sys.path.insert(0, str(d / "yt-dlp"))   # yt-dlp es un zip ejecutable de Python
            return
