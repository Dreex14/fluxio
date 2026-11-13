#!/usr/bin/env python3
"""
Fluxio · descargador de vídeo, audio y GIF  (PySide6 + yt-dlp + ffmpeg)

Instalación:   pip install -r requirements.txt
Ejecución:     pythonw main.pyw      (o python main.pyw para ver los mensajes en consola)

El código está en la carpeta fluxio/ (ver README.md).
"""
from fluxio import paths

paths.fix_std_streams()
paths.load_ytdlp_override()   # debe ir antes de cualquier import de yt_dlp

from fluxio.app import main  # noqa: E402

if __name__ == "__main__":
    main()
