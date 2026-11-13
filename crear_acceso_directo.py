"""
Crea un acceso directo «Fluxio» en el menú Inicio de Windows, con su icono y sin
ventana de consola.

Uso (una sola vez, desde la carpeta del proyecto):
    python crear_acceso_directo.py
"""
import os
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
script = next((here / n for n in ("main.pyw", "main.py") if (here / n).exists()), None)
if script is None:
    sys.exit("No encuentro main.pyw en esta carpeta.")

pythonw = Path(sys.executable).with_name("pythonw.exe")
if not pythonw.exists():
    sys.exit("No encuentro pythonw.exe. Ejecuta esto en Windows con Python instalado.")

start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
lnk = start_menu / "Fluxio.lnk"
icon = here / "icon.ico"


def q(p):  # escapar comillas simples para PowerShell
    return str(p).replace("'", "''")


ps = (
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
    "$s.TargetPath='{exe}';"
    "$s.Arguments='\"{script}\"';"
    "$s.WorkingDirectory='{wd}';"
    "$s.IconLocation='{icon}';"
    "$s.Description='Fluxio: descargador de vídeo, audio y GIF';"
    "$s.Save()"
).format(lnk=q(lnk), exe=q(pythonw), script=q(script), wd=q(here), icon=q(icon))

subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
print(f"Acceso directo creado en: {lnk}")
print("Búscalo en Inicio; con clic derecho puedes anclarlo a Inicio o a la barra de tareas.")
