"""Formato de datos y operaciones con ffmpeg: volumen, normalización, GIF y recorte.

No depende de Qt ni de yt-dlp, así que se puede usar y probar por separado.
"""
import html as htmllib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .paths import APP_DIR

ANSI = re.compile(r"\x1b\[[0-9;]*m")
NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".flac", ".wav", ".webm"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".3gp"}


class Cancelled(Exception):
    """El usuario canceló una operación de ffmpeg."""


# ───────────────────────── formato de datos ─────────────────────────
def clean(text) -> str:
    s = ANSI.sub("", str(text)).strip()
    return re.sub(r"^ERROR:\s*", "", s)


def fmt_speed(bps):
    if not bps:
        return ""
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024:
            return f"{bps:.1f} {unit}"
        bps /= 1024
    return f"{bps:.1f} TB/s"


def fmt_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_eta(sec):
    if sec is None:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_dur(sec):
    return fmt_eta(sec) if sec else ""


def fmt_clock(sec):
    """Segundos → '1:02.5' o '1:02:03.5' (con décimas), para el editor de corte."""
    sec = max(0.0, float(sec or 0))
    tenths = int(round(sec * 10))
    s, t = divmod(tenths, 10)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}.{t}" if h else f"{m}:{s:02d}.{t}"


def parse_time(text: str):
    """'90', '1:30', '1:30.5' o '1:02:03' → segundos. Vacío → None.
    Lanza ValueError si el texto no es un tiempo válido."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    parts = [float(p) for p in text.split(":")]
    if len(parts) > 3 or any(p < 0 for p in parts):
        raise ValueError(text)
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def safe_name(s, maxlen=80):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    return s[:maxlen] or "archivo"


def unique_path(p):
    p = Path(p)
    cand, n = p, 1
    while cand.exists():
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        n += 1
    return cand


# ───────────────────────── ffmpeg ─────────────────────────
def find_ffmpeg(custom: str = ""):
    if custom and Path(custom).exists():
        return custom
    for name in ("ffmpeg.exe", "ffmpeg"):   # ffmpeg junto al programa (versión portable)
        local = APP_DIR / name
        if local.is_file():
            return str(local)
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def run_ffmpeg(ffmpeg, args):
    return subprocess.run([ffmpeg, "-hide_banner", "-nostdin", *args],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace", **NO_WINDOW)


def run_ffmpeg_progress(ffmpeg, args, duration, on_progress=None, should_cancel=None):
    """Ejecuta ffmpeg informando del avance (0-100) y permitiendo cancelar.
    Devuelve (código de salida, stderr)."""
    with tempfile.TemporaryFile() as errf:
        p = subprocess.Popen([ffmpeg, "-hide_banner", "-nostdin", "-nostats",
                              "-progress", "pipe:1", *args],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf,
                             text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
        try:
            for line in p.stdout:
                if should_cancel and should_cancel():
                    p.kill()
                    p.wait()
                    raise Cancelled()
                if on_progress and duration and line.startswith("out_time="):
                    try:
                        t = parse_time(line.split("=", 1)[1].strip())
                    except ValueError:
                        continue
                    if t is not None:
                        on_progress(min(100.0, t / duration * 100))
            p.wait()
        except BaseException:
            if p.poll() is None:
                p.kill()
                p.wait()
            raise
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")
    return p.returncode, err


def probe_media(ffmpeg, path):
    """(duración en segundos o None, ¿tiene pista de vídeo real?)"""
    r = run_ffmpeg(ffmpeg, ["-i", str(path)])
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    videos = re.findall(r"Stream #.*?Video:.*", r.stderr)
    return dur, any("attached pic" not in v for v in videos)


def probe_fps(ffmpeg, path):
    r = run_ffmpeg(ffmpeg, ["-i", str(path)])
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", r.stderr)
    return float(m.group(1)) if m else None


def probe_audio(ffmpeg, path):
    r = run_ffmpeg(ffmpeg, ["-i", str(path)])
    br = re.search(r"bitrate:\s*(\d+) kb/s", r.stderr)
    codec = re.search(r"Audio:\s*(\w+)", r.stderr)
    return (int(br.group(1)) if br else None), (codec.group(1) if codec else "")


# ───────────────────────── volumen ─────────────────────────
GAIN_MIN, GAIN_MAX = -30.0, 30.0
# Spotify (calidad «Normal») normaliza a -14 LUFS integrados, pico real máx. -1 dBTP.
NORM_LUFS, NORM_TP, NORM_LRA = -14.0, -1.0, 11.0


def gain_filter(db):
    """Filtro para subir/bajar `db` decibelios. Al subir se añade un limitador de picos."""
    f = f"volume={db:g}dB"
    if db > 0:
        f += ",alimiter=limit=0.97:level=disabled"
    return f


def audio_stats(ffmpeg, path):
    """(volumen medio, pico máximo) en dB de un archivo de audio."""
    r = run_ffmpeg(ffmpeg, ["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"])
    mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    peak = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    if r.returncode != 0 or not (mean and peak):
        raise RuntimeError("no se pudo medir el volumen: " + r.stderr[-300:])
    return float(mean.group(1)), float(peak.group(1))


def audio_codec_args(ext, kbps, src_codec=""):
    k = f"{min(max(kbps or 192, 64), 320)}k"
    ext = ext.lower()
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", k, "-id3v2_version", "3"]
    if ext in (".m4a", ".aac"):
        return ["-c:a", "aac", "-b:a", k]
    if ext in (".opus", ".webm"):
        return ["-c:a", "libopus", "-b:a", k]
    if ext == ".ogg":
        return ["-c:a", "libvorbis", "-b:a", k]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext == ".wav":
        return ["-c:a", src_codec if src_codec.startswith("pcm_") else "pcm_s16le"]
    raise RuntimeError(f"formato de audio no compatible: {ext}")


def _cover_maps(ext):
    # la portada (si existe) se conserva copiándola tal cual
    return (["-map", "0:a", "-map", "0:v?", "-c:v", "copy"]
            if ext in (".mp3", ".m4a", ".flac") else ["-map", "0:a"])


def change_volume(ffmpeg, src, db, dst=None, log=lambda m: None):
    """Sube/baja `db` decibelios el audio `src`. Con `dst=None` sustituye el original;
    si no, escribe en `dst` y deja el original intacto. Devuelve la ruta resultante."""
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in AUDIO_EXTS:
        raise RuntimeError(f"formato de audio no compatible: {ext or src.name}")
    kbps, scodec = probe_audio(ffmpeg, src)
    codec = audio_codec_args(ext, kbps, scodec)
    inplace = dst is None
    out = src.with_name(src.stem + ".vol" + ext) if inplace else Path(dst)
    r = run_ffmpeg(ffmpeg, ["-y", "-i", str(src), *_cover_maps(ext), "-map_metadata", "0",
                            "-af", gain_filter(db), *codec, str(out)])
    if r.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise RuntimeError("falló el ajuste de volumen: " + r.stderr[-300:])
    if inplace:
        out.replace(src)
        out = src
    log(f"Volumen {db:+g} dB aplicado: {out.name}")
    return out


def normalize_audio(ffmpeg, path, afmt, abr, log=lambda m: None, gain_db=0.0):
    """Normaliza `path` a -14 LUFS con loudnorm en dos pasadas (sustituye el archivo)."""
    path = Path(path)
    base = f"loudnorm=I={NORM_LUFS}:TP={NORM_TP}:LRA={NORM_LRA}"
    # pasada 1: medir
    r = run_ffmpeg(ffmpeg, ["-i", str(path), "-vn", "-af", base + ":print_format=json",
                            "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if r.returncode != 0 or not m:
        raise RuntimeError("no se pudo medir el volumen: " + r.stderr[-300:])
    st = json.loads(m.group(0))
    if st["input_i"] in ("-inf", "inf"):
        log("⚠ Audio en silencio: no se normaliza")
        return
    rate = re.search(r"(\d{4,6}) Hz", r.stderr)
    rate = int(rate.group(1)) if rate else 44100
    if afmt == "opus":
        rate = 48000   # opus solo admite 48 kHz
    # pasada 2: aplicar con los valores medidos (loudnorm sube a 192 kHz si no se fija -ar)
    flt = (f"{base}:measured_I={st['input_i']}:measured_TP={st['input_tp']}"
           f":measured_LRA={st['input_lra']}:measured_thresh={st['input_thresh']}"
           f":offset={st['target_offset']}:linear=true")
    if gain_db:
        flt += "," + gain_filter(gain_db)
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", f"{abr}k", "-id3v2_version", "3"],
             "m4a": ["-c:a", "aac", "-b:a", f"{abr}k"],
             "opus": ["-c:a", "libopus", "-b:a", f"{abr}k"],
             "flac": ["-c:a", "flac"],
             "wav": ["-c:a", "pcm_s16le"]}[afmt]
    tmp = path.with_name(path.stem + ".norm" + path.suffix)
    r = run_ffmpeg(ffmpeg, ["-y", "-i", str(path), "-map", "0:a", "-map", "0:v?",
                            "-c:v", "copy", "-map_metadata", "0", "-af", flt,
                            "-ar", str(rate), *codec, str(tmp)])
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("falló la normalización: " + r.stderr[-300:])
    tmp.replace(path)
    extra = f" {gain_db:+g} dB" if gain_db else ""
    log(f"Volumen normalizado ({float(st['input_i']):.1f} → {NORM_LUFS:.0f} LUFS){extra}")


# ───────────────────────── recorte manual ─────────────────────────
def _cut_args(src, out, start, dur, precise, has_video, ext, subs=True, ffmpeg=None):
    args = ["-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}"]
    if not precise:
        args += (["-map", "0"] if subs else ["-map", "0:v?", "-map", "0:a?"])
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    elif has_video:
        if ext == ".webm":
            v = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
                 "-deadline", "good", "-cpu-used", "4"]
            a = ["-c:a", "libopus", "-b:a", "160k"]
        else:
            v = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            a = ["-c:a", "aac", "-b:a", "192k"]
        args += ["-map", "0:v:0", "-map", "0:a?"]
        args += (["-map", "0:s?", "-c:s", "copy"] if subs else ["-sn"])
        args += [*v, *a]
        if ext in (".mp4", ".m4v", ".mov"):
            args += ["-movflags", "+faststart"]
    else:
        kbps, scodec = probe_audio(ffmpeg, src)
        args += [*_cover_maps(ext), *audio_codec_args(ext, kbps, scodec)]
    return args + ["-map_metadata", "0", str(out)]


def cut_media(ffmpeg, src, start, end, precise=False, dst=None,
              on_progress=None, should_cancel=None):
    """Recorta `src` entre `start` y `end` (segundos).

    precise=False: copia sin recodificar (instantáneo y sin pérdida; el inicio se ajusta
    al fotograma clave anterior). precise=True: recodifica para cortar en el punto exacto.
    Con `dst=None` sustituye el original. Devuelve la ruta resultante."""
    src = Path(src)
    ext = src.suffix.lower()
    dur = end - start
    if dur <= 0:
        raise ValueError("El final del corte debe ser posterior al inicio.")
    _, has_video = probe_media(ffmpeg, src)
    if not has_video and precise and ext not in AUDIO_EXTS:
        raise RuntimeError(f"formato no compatible para recodificar: {ext}")
    inplace = dst is None
    out = src.with_name(src.stem + ".cut.tmp" + ext) if inplace else Path(dst)

    rc, err = 1, ""
    for subs in (True, False):   # si los subtítulos dan problemas, se reintenta sin ellos
        args = _cut_args(src, out, start, dur, precise, has_video, ext, subs, ffmpeg)
        try:
            rc, err = run_ffmpeg_progress(ffmpeg, args, dur, on_progress, should_cancel)
        except Cancelled:
            out.unlink(missing_ok=True)
            raise
        if rc == 0 and out.exists():
            break
    if rc != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise RuntimeError("falló el corte: " + err[-400:])
    if inplace:
        out.replace(src)
        out = src
    return out


# ───────────────────────── GIF ─────────────────────────
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
GENERIC_GIF_NAMES = {"giphy", "giphy-downsized", "giphy-downsized-large", "200", "source",
                     "image", "tenor", "download", "original", "animated"}
GIF_AUTO_WIDTH = 640    # los vídeos se reducen a esto si no se elige ancho
GIF_AUTO_FPS = 20       # ...y se limitan a estos fotogramas por segundo


def gif_candidates(url):
    """Enlaces directos al GIF que se deducen de la propia URL, sin analizar la página
    (enlaces .gif, .gifv de Imgur y páginas de Giphy)."""
    u = urlparse(url)
    path, host = u.path, u.netloc.lower()
    out = []
    if path.lower().endswith(".gif"):
        out.append(url)
    elif path.lower().endswith(".gifv"):
        out.append(u._replace(path=path[:-1]).geturl())
    if host.endswith("giphy.com") and "/media/" not in path:
        m = re.search(r"/(?:gifs|stickers|embed)/(?:[^/]*-)?([A-Za-z0-9]+)/?$", path)
        if m:
            out.append(f"https://media.giphy.com/media/{m.group(1)}/giphy.gif")
    return list(dict.fromkeys(out))


def http_open(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout)


def page_media_candidates(url, page_html):
    """(título, enlaces) con las imágenes y vídeos que la página declara en sus <meta>
    (og:image, og:video, twitter:image…). Los GIF van primero."""
    gifs, vids = [], []
    for tag in re.findall(r"<meta\s[^>]*>", page_html, re.I):
        attrs = {}
        for k, v1, v2 in re.findall(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", tag):
            attrs[k.lower()] = v1 or v2
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        val = htmllib.unescape(attrs.get("content", "")).strip()
        if not val:
            continue
        val = urljoin(url, val)
        if key in ("og:image", "og:image:url", "og:image:secure_url",
                   "twitter:image", "twitter:image:src"):
            if not re.search(r"_s\.gif|still", val, re.I):   # descarta las imágenes fijas
                gifs.append(val)
        elif key in ("og:video", "og:video:url", "og:video:secure_url",
                     "twitter:player:stream"):
            vids.append(val)
    t = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
    title = htmllib.unescape(t.group(1)).strip() if t else ""
    title = title.split(" | ")[0].split(" - ")[0].strip()
    return title, list(dict.fromkeys(gifs + vids))


def video_to_gif(ffmpeg, src, dst, fps=None, width=None, start=None, end=None):
    """Convierte un vídeo (o redimensiona/recorta un GIF) a GIF con paleta optimizada."""
    pre = []
    if fps:
        pre.append(f"fps={fps}")
    if width:
        pre.append(f"scale='min({width},iw)':-1:flags=lanczos")
    pre.append("split[a][b]")
    graph = (f"[0:v]{','.join(pre)};[a]palettegen=stats_mode=diff[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    args = ["-y"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        dur = end - (start or 0)
        if dur <= 0:
            raise ValueError("El fin del fragmento debe ser posterior al inicio")
        args += ["-t", f"{dur:.3f}"]
    args += ["-i", str(src), "-filter_complex", graph, "-an", "-loop", "0", str(dst)]
    r = run_ffmpeg(ffmpeg, args)
    if r.returncode != 0 or not Path(dst).exists():
        Path(dst).unlink(missing_ok=True)
        raise RuntimeError("falló la conversión a GIF: " + r.stderr[-300:])
