"""Motor de descargas: opciones de yt-dlp, hilos de descarga y gestor de la cola."""
import io
import re
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled, download_range_func
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from . import media as M
from .paths import FROZEN, YTDLP_URL, ytdlp_update_dirs

# ───────────────────────── plantilla de nombre ─────────────────────────
TEMPLATE_TOKENS = {
    "titulo": ("%(title).80B", "Mi vídeo"),
    "canal": ("%(uploader,channel|Desconocido).60B", "Canal"),
    "id": ("%(id)s", "dQw4w9WgXcQ"),
    "fecha": ("%(upload_date>%Y-%m-%d|sin fecha)s", "2026-11-11"),
    "sitio": ("%(extractor_key)s", "Youtube"),
    "resolucion": ("%(resolution|)s", "1920x1080"),
}
DEFAULT_TEMPLATE = "{titulo} [{id}]"
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def _template_parts(template):
    t = (template or "").strip() or DEFAULT_TEMPLATE
    t = _BAD_CHARS.sub("_", t.replace("\\", "/")).strip("/ ")
    return t or DEFAULT_TEMPLATE


def template_to_outtmpl(template):
    """'{canal} - {titulo}' → plantilla de salida de yt-dlp."""
    t = _template_parts(template).replace("%", "%%")
    t = re.sub(r"\{(\w+)\}",
               lambda m: TEMPLATE_TOKENS.get(m.group(1).lower(), (m.group(0),))[0], t)
    return t + ".%(ext)s"


def template_example(template, ext="mp4"):
    t = _template_parts(template)
    t = re.sub(r"\{(\w+)\}",
               lambda m: TEMPLATE_TOKENS.get(m.group(1).lower(), (0, m.group(0)))[1], t)
    return f"{t}.{ext}"


# ───────────────────────── descripción de una configuración ─────────────────────────
def describe_cfg(cfg, long=False):
    """Resumen corto de lo que se va a descargar: «MP4 · 1080p · Subs»."""
    mode = cfg["mode"]
    parts = []
    if mode == "video":
        parts.append(cfg["vfmt"].upper())
        if cfg.get("format_id"):
            parts.append(cfg.get("format_label") or f"formato {cfg['format_id']}")
        else:
            parts.append(f"{cfg['height']}p" if cfg.get("height") else
                         ("Mejor calidad" if long else "Mejor"))
        if cfg.get("subs"):
            parts.append(f"Subs {cfg.get('sublangs', '')}".strip() if long else "Subs")
    elif mode == "audio":
        parts.append(cfg["afmt"].upper())
        if cfg.get("format_id"):
            parts.append(cfg.get("format_label") or f"formato {cfg['format_id']}")
        elif cfg["afmt"] in ("mp3", "m4a", "opus"):
            parts.append(f"{cfg['abr']} kbps")
        if cfg.get("norm"):
            parts.append("Normalizado")
        if cfg.get("gain"):
            parts.append(f"{cfg['gain']:+g} dB")
    else:
        parts.append("GIF")
        w, f = cfg.get("gif_width"), cfg.get("gif_fps")
        if w or f or long:
            parts.append(f"{w} px" if w else "Ancho auto")
            parts.append(f"{f} fps" if f else "fps auto")
    if cfg.get("cut"):
        a = cfg.get("start") or "0"
        b = cfg.get("end") or "fin"
        parts.append(f"Corte {a}–{b}")
    if cfg.get("playlist"):
        parts.append("Lista completa" if long else "Lista")
    return " · ".join(parts)


# ───────────────────────── postprocesadores de audio ─────────────────────────
class NormalizePP(yt_dlp.postprocessor.PostProcessor):
    """Normaliza el volumen del audio ya extraído (y aplica, opcionalmente, `gain` dB)."""

    def __init__(self, downloader, ffmpeg, afmt, abr, log, gain=0.0):
        super().__init__(downloader)
        self.ffmpeg, self.afmt, self.abr, self.log, self.gain = ffmpeg, afmt, abr, log, gain

    def run(self, info):
        path = info.get("filepath")
        if path and Path(path).exists():
            try:
                M.normalize_audio(self.ffmpeg, path, self.afmt, self.abr, self.log, self.gain)
            except Exception as e:
                self.log("⚠ " + M.clean(e))   # se conserva el audio sin normalizar
        return [], info


class VolumePP(yt_dlp.postprocessor.PostProcessor):
    """Sube/baja el volumen del audio ya extraído."""

    def __init__(self, downloader, ffmpeg, gain, log):
        super().__init__(downloader)
        self.ffmpeg, self.gain, self.log = ffmpeg, gain, log

    def run(self, info):
        path = info.get("filepath")
        if path and Path(path).exists():
            try:
                M.change_volume(self.ffmpeg, path, self.gain, None, self.log)
            except Exception as e:
                self.log("⚠ " + M.clean(e))
        return [], info


def add_audio_pps(y, cfg, log):
    """Coloca la normalización / el ajuste de volumen justo después de extraer el audio
    (antes de metadatos y portada, que así se escriben sobre el archivo final)."""
    gain = float(cfg.get("gain") or 0)
    if cfg.get("norm"):
        pp = NormalizePP(y, cfg["ffmpeg"], cfg["afmt"], cfg["abr"], log, gain)
    elif gain:
        pp = VolumePP(y, cfg["ffmpeg"], gain, log)
    else:
        return
    y.add_post_processor(pp, when="post_process")
    try:
        lst = y._pps["post_process"]
        lst.remove(pp)
        idx = next(i for i, p in enumerate(lst)
                   if type(p).__name__ == "FFmpegExtractAudioPP") + 1
        lst.insert(idx, pp)
    except Exception:
        pass   # si cambia la API interna, se ejecuta al final (también funciona)


# ───────────────────────── opciones de yt-dlp ─────────────────────────
class _Logger:
    def __init__(self, emit, soft=False):
        self.emit, self.soft = emit, soft

    def debug(self, msg):
        msg = M.clean(msg)
        if msg and not msg.startswith("[download]"):
            self.emit(msg)

    def info(self, msg):
        self.debug(msg)

    def warning(self, msg):
        self.emit("⚠ " + M.clean(msg))

    def error(self, msg):
        # con un plan B (GIF) los errores se registran como avisos
        self.emit(("⚠ " if self.soft else "✖ ") + M.clean(msg))


def build_opts(cfg: dict, hook, logger) -> dict:
    outdir = Path(cfg["outdir"])
    name = template_to_outtmpl(cfg.get("template"))
    if cfg["playlist"]:
        name = "%(playlist_title|)s/%(playlist_index|)s " + name
    o = {
        "outtmpl": str(outdir / name),
        "noplaylist": not cfg["playlist"],
        "progress_hooks": [hook],
        "logger": logger,
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "retries": 5,
    }
    if cfg["ffmpeg"]:
        o["ffmpeg_location"] = cfg["ffmpeg"]
    if cfg.get("ffhls") and cfg["ffmpeg"]:
        o["hls_prefer_native"] = False   # ffmpeg descarga los streams HLS
    if cfg.get("cookies"):
        o["cookiesfrombrowser"] = (cfg["cookies"],)

    pps = []
    gif = cfg["mode"] == "gif"
    audio = cfg["mode"] == "audio"
    fid = cfg.get("format_id")
    if gif:
        # solo hace falta el vídeo (sin audio); ≤720p basta de sobra para un GIF
        o["format"] = "bv*[height<=720]/b[height<=720]/bv*/b"
    elif audio:
        o["format"] = fid or "bestaudio/best"
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": cfg["afmt"]}
        if cfg["afmt"] in ("mp3", "m4a", "opus"):
            pp["preferredquality"] = str(cfg["abr"])
        pps.append(pp)
    else:
        h = cfg["height"]
        if fid:
            o["format"] = fid if cfg.get("format_has_audio") else f"{fid}+ba/{fid}"
        else:
            o["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/best" if h else "bv*+ba/b"
        o["merge_output_format"] = cfg["vfmt"]

    if cfg["subs"] and cfg["mode"] == "video":
        o["writesubtitles"] = True
        o["writeautomaticsub"] = True
        o["subtitleslangs"] = [s.strip() for s in cfg["sublangs"].split(",") if s.strip()]
        pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
        if cfg["embed_subs"]:
            pps.append({"key": "FFmpegEmbedSubtitle"})

    if cfg["meta"] and not gif:
        pps.append({"key": "FFmpegMetadata", "add_chapters": True})

    thumb_ok = not (audio and cfg["afmt"] == "wav") and not (
        cfg["mode"] == "video" and cfg["vfmt"] == "webm")
    if cfg["thumb"] and thumb_ok and not gif:
        o["writethumbnail"] = True
        pps.append({"key": "EmbedThumbnail"})   # siempre el último

    if cfg["cut"]:
        start = M.parse_time(cfg["start"]) or 0
        end = M.parse_time(cfg["end"]) or float("inf")
        o["download_ranges"] = download_range_func(None, [(start, end)])
        o["force_keyframes_at_cuts"] = True

    o["postprocessors"] = pps
    return o


# ───────────────────────── análisis de enlaces ─────────────────────────
def _codec(c):
    c = (c or "").lower()
    for k, v in (("avc", "H.264"), ("h264", "H.264"), ("hev", "H.265"), ("hvc", "H.265"),
                 ("h265", "H.265"), ("vp09", "VP9"), ("vp9", "VP9"), ("vp8", "VP8"),
                 ("av01", "AV1"), ("mp4a", "AAC"), ("aac", "AAC"), ("opus", "Opus"),
                 ("vorbis", "Vorbis"), ("mp3", "MP3"), ("flac", "FLAC")):
        if c.startswith(k):
            return v
    return "" if c in ("", "none") else c.split(".")[0]


def summarize_formats(info):
    """Listas (vídeo, audio) de formatos reales: dicts con id, label, short, audio."""
    video, audio = [], []
    for f in info.get("formats") or []:
        fid = f.get("format_id")
        if not fid or f.get("ext") == "mhtml" or "storyboard" in (f.get("format_note") or ""):
            continue
        vc, ac = f.get("vcodec"), f.get("acodec")
        has_v = vc != "none" and (vc is not None or bool(f.get("height")))
        has_a = ac != "none" and (ac is not None or not has_v)
        size = M.fmt_size(f.get("filesize") or f.get("filesize_approx"))
        if has_v:
            h, fps = f.get("height"), f.get("fps") or 0
            res = f"{h}p{int(fps) if fps > 30 else ''}" if h else (f.get("format_note") or fid)
            bits = [res, _codec(vc), f.get("ext") or "", size, "con audio" if has_a else ""]
            video.append(dict(id=fid, audio=has_a, short=res,
                              label="  ·  ".join(b for b in bits if b),
                              key=(h or 0, fps, f.get("tbr") or 0)))
        elif has_a:
            abr = f.get("abr")
            kb = f"{int(abr)} kbps" if abr else (f.get("format_note") or fid)
            bits = [_codec(ac) or "audio", kb, f.get("ext") or "", size]
            audio.append(dict(id=fid, audio=True, short=f"{_codec(ac)} {kb}".strip(),
                              label="  ·  ".join(b for b in bits if b),
                              key=(abr or 0, f.get("tbr") or 0)))
    video.sort(key=lambda d: d["key"], reverse=True)
    audio.sort(key=lambda d: d["key"], reverse=True)
    return video, audio


class InfoWorker(QThread):
    done = Signal(str, dict, bytes)   # url, info resumida, miniatura
    failed = Signal(str, str)         # url, mensaje

    def __init__(self, url, cookies=None):
        super().__init__()
        self.url, self.cookies = url, cookies

    def run(self):
        try:
            opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                    "skip_download": True, "logger": _Logger(lambda m: None)}
            if self.cookies:
                opts["cookiesfrombrowser"] = (self.cookies,)
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(self.url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                first = next((e for e in info["entries"] if e), None) or {}
                n = info.get("playlist_count") or len(list(info.get("entries") or []))
                info = {**first, "title": info.get("title") or first.get("title"),
                        "playlist_count": n}
            data = b""
            thumb = info.get("thumbnail")
            if thumb:
                try:
                    req = urllib.request.Request(thumb, headers={"User-Agent": M.UA})
                    data = urllib.request.urlopen(req, timeout=10).read()
                except Exception:
                    pass
            video, audio = summarize_formats(info)
            out = {k: info.get(k) for k in ("title", "uploader", "channel", "duration",
                                            "extractor_key", "playlist_count")}
            out["video_formats"], out["audio_formats"] = video, audio
            self.done.emit(self.url, out, data)
        except Exception as e:
            self.failed.emit(self.url, M.clean(e))


# ───────────────────────── trabajos auxiliares ─────────────────────────
class FuncWorker(QThread):
    """Ejecuta una función en segundo plano y devuelve su resultado."""
    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self):
        try:
            self.ok.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            self.failed.emit(M.clean(e))


class CutWorker(QThread):
    """Recorta un archivo con ffmpeg informando del progreso; se puede cancelar."""
    progress = Signal(float)
    ok = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, ffmpeg, src, start, end, precise, dst):
        super().__init__()
        self.a = (ffmpeg, src, start, end, precise, dst)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ffmpeg, src, start, end, precise, dst = self.a
        try:
            out = M.cut_media(ffmpeg, src, start, end, precise, dst,
                              on_progress=self.progress.emit,
                              should_cancel=lambda: self._cancel)
            self.ok.emit(str(out))
        except M.Cancelled:
            self.cancelled.emit()
        except Exception as e:
            self.failed.emit(M.clean(e))


class UpdateWorker(QThread):
    done = Signal(bool, str)

    def run(self):
        try:
            self.done.emit(*(self._update_exe() if FROZEN else self._update_pip()))
        except Exception as e:
            self.done.emit(False, M.clean(e))

    @staticmethod
    def _update_pip():
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=300, **M.NO_WINDOW)
        return r.returncode == 0, M.clean(r.stdout)[-1500:]

    @staticmethod
    def _update_exe():
        """En el .exe no hay pip: se baja la última versión de yt-dlp desde GitHub y se
        guarda junto al programa; la app la carga al arrancar."""
        with M.http_open(YTDLP_URL, timeout=60) as r:
            data = r.read()
        try:   # la versión se lee del propio paquete descargado (y de paso se valida)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                src = z.read("yt_dlp/version.py").decode("utf-8", "replace")
            version = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", src).group(1)
        except Exception:
            raise RuntimeError("la descarga de yt-dlp no es válida")
        last = None
        for d in ytdlp_update_dirs():
            try:
                d.mkdir(parents=True, exist_ok=True)
                tmp = d / "yt-dlp.tmp"
                tmp.write_bytes(data)
                tmp.replace(d / "yt-dlp")
                (d / "version.txt").write_text(version)
                return True, f"yt-dlp {version} descargado en {d}"
            except OSError as e:
                last = e
        raise RuntimeError(f"no se pudo guardar yt-dlp: {last}")


# ───────────────────────── descarga de un trabajo ─────────────────────────
class DownloadThread(QThread):
    progress = Signal(int, float, float, str)   # id, % (-1 = desconocido), B/s, ETA o tamaño
    status = Signal(int, str, str)              # id, fase (running/processing), texto
    title = Signal(int, str)
    log = Signal(int, str)
    file_done = Signal(int, str)
    result = Signal(int, str, str)              # id, done/error/cancelled, mensaje

    def __init__(self, jid, url, cfg):
        super().__init__()
        self.jid, self.url, self.cfg = jid, url, cfg
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _log(self, msg):
        self.log.emit(self.jid, msg)

    def _status(self, phase, text):
        self.status.emit(self.jid, phase, text)

    def run(self):
        try:
            if self.cfg["mode"] == "gif":
                self._download_gif()
            else:
                opts = build_opts(self.cfg, self._hook, _Logger(self._log))
                opts["post_hooks"] = [lambda p: self.file_done.emit(self.jid, str(p))]
                with yt_dlp.YoutubeDL(opts) as y:
                    c = self.cfg
                    if c["mode"] == "audio" and c["ffmpeg"] and (c.get("norm") or c.get("gain")):
                        add_audio_pps(y, c, self._log)
                    y.download([self.url])
            self.result.emit(self.jid, "done", "")
        except DownloadCancelled:
            self.result.emit(self.jid, "cancelled", "")
        except Exception as e:
            if self._cancel:
                self.result.emit(self.jid, "cancelled", "")
            else:
                msg = M.clean(e)
                self._log("✖ " + msg)
                self._log(traceback.format_exc())
                self.result.emit(self.jid, "error", msg)

    def _hook(self, d):
        if self._cancel:
            raise DownloadCancelled()
        info = d.get("info_dict") or {}
        if info.get("title"):
            self.title.emit(self.jid, info["title"])
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = (d.get("downloaded_bytes", 0) / total * 100) if total else -1
            label = "Descargando"
            idx, n = info.get("playlist_index"), info.get("n_entries")
            if idx and n:
                label += f" {idx}/{n}"
            self._status("running", label)
            eta = M.fmt_eta(d.get("eta")) if total else M.fmt_size(d.get("downloaded_bytes"))
            self.progress.emit(self.jid, pct, float(d.get("speed") or 0), eta)
        elif d["status"] == "finished":
            self.progress.emit(self.jid, 100, 0.0, "")
            self._status("processing", "Procesando…")

    # ── GIF ──
    def _download_gif(self):
        """1) enlace directo a un GIF → 2) yt-dlp (tweets, Reddit, YouTube… → se convierte)
        → 3) buscar el GIF/vídeo que declara la página (Tenor, etc.)."""
        cfg = self.cfg
        outdir = Path(cfg["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        for cand in M.gif_candidates(self.url):
            try:
                src = self._fetch_media(cand, outdir)
            except DownloadCancelled:
                raise
            except Exception as e:
                self._log(f"⚠ {cand}: {M.clean(e)}")
                continue
            self._finish_gif(src, apply_cut=True)
            return

        ytdlp_err = ""
        try:
            files = []
            opts = build_opts(cfg, self._hook, _Logger(self._log, soft=True))
            opts["post_hooks"] = [files.append]
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([self.url])
            if not files:
                raise RuntimeError("yt-dlp no descargó ningún archivo")
        except DownloadCancelled:
            raise
        except Exception as e:
            if self._cancel:
                raise DownloadCancelled()
            ytdlp_err = M.clean(e)
            self._log("⚠ yt-dlp: " + ytdlp_err)
        else:
            for f in files:
                self._finish_gif(Path(f), apply_cut=False)   # yt-dlp ya recortó
            return

        self._status("running", "Buscando GIF en la página…")
        self.progress.emit(self.jid, -1, 0.0, "")
        try:
            with M.http_open(self.url) as r:
                ctype = r.headers.get_content_type()
                if ctype == "image/gif" or ctype.startswith("video/"):
                    src = self._save_response(r, self.url, outdir)
                    self._finish_gif(src, apply_cut=True)
                    return
                raw = r.read(2_000_000)
                charset = r.headers.get_content_charset() or "utf-8"
            title, cands = M.page_media_candidates(self.url, raw.decode(charset, "replace"))
        except DownloadCancelled:
            raise
        except Exception as e:
            self._log("⚠ " + M.clean(e))
            cands, title = [], ""
        for cand in cands:
            try:
                src = self._fetch_media(cand, outdir, title)
            except DownloadCancelled:
                raise
            except Exception as e:
                self._log(f"⚠ {cand}: {M.clean(e)}")
                continue
            self._finish_gif(src, apply_cut=True)
            return
        raise RuntimeError(ytdlp_err or "No se encontró ningún GIF ni vídeo en ese enlace.")

    def _fetch_media(self, url, outdir, title=None):
        with M.http_open(url) as r:
            return self._save_response(r, url, outdir, title)

    def _save_response(self, r, url, outdir, title=None):
        ctype = r.headers.get_content_type()
        if ctype == "image/gif":
            ext = ".gif"
        elif ctype.startswith("video/"):
            ext = {"video/mp4": ".mp4", "video/webm": ".webm",
                   "video/quicktime": ".mov"}.get(ctype, ".mp4")
        else:
            raise RuntimeError(f"no es un GIF ni un vídeo ({ctype})")
        p = Path(unquote(urlparse(url).path))
        stem = title or p.stem or "gif"
        if not title and stem.lower() in M.GENERIC_GIF_NAMES and p.parent.name:
            stem = f"{stem} [{p.parent.name}]"
        dest = M.unique_path(Path(outdir) / (M.safe_name(stem) + ext))
        self.title.emit(self.jid, dest.stem)
        self._status("running", "Descargando")
        total = int(r.headers.get("Content-Length") or 0)
        done, t0, last = 0, time.time(), 0.0
        try:
            with open(dest, "wb") as f:
                while True:
                    if self._cancel:
                        raise DownloadCancelled()
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 0.15:
                        last = now
                        spd = done / max(now - t0, 1e-6)
                        pct = done / total * 100 if total else -1
                        eta = M.fmt_eta((total - done) / spd) if total and spd else M.fmt_size(done)
                        self.progress.emit(self.jid, pct, spd, eta)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        self.progress.emit(self.jid, 100, 0.0, "")
        return dest

    def _finish_gif(self, src, apply_cut):
        """Deja `src` como GIF final: lo conserva si ya es GIF y no se pidió ningún cambio;
        si no, lo (re)codifica con ffmpeg."""
        cfg = self.cfg
        ff = cfg["ffmpeg"]
        src = Path(src)
        is_gif = src.suffix.lower() == ".gif"
        cut = bool(cfg.get("cut")) and apply_cut
        start = (M.parse_time(cfg["start"]) or None) if cut else None
        end = (M.parse_time(cfg["end"]) or None) if cut else None
        width, fps = cfg.get("gif_width"), cfg.get("gif_fps")
        if is_gif and not (width or fps or cut):
            self.file_done.emit(self.jid, str(src))
            return src
        if not ff:
            if is_gif:
                self._log("⚠ Sin ffmpeg: se guarda el GIF original sin cambios")
                self.file_done.emit(self.jid, str(src))
                return src
            raise RuntimeError("Hace falta ffmpeg para convertir vídeo a GIF "
                               f"(el vídeo se ha guardado sin convertir: {src.name})")
        if not is_gif:   # los vídeos se limitan por defecto para que el GIF no pese demasiado
            width = width or M.GIF_AUTO_WIDTH
            if not fps:
                src_fps = M.probe_fps(ff, src)
                fps = M.GIF_AUTO_FPS if src_fps and src_fps > M.GIF_AUTO_FPS else None
        self._status("processing", "Convirtiendo a GIF…")
        self.progress.emit(self.jid, -1, 0.0, "")
        dst = src.with_name(src.stem + ".tmp.gif") if is_gif else M.unique_path(src.with_suffix(".gif"))
        M.video_to_gif(ff, src, dst, fps, width, start, end)
        if is_gif:
            dst.replace(src)
            dst = src
        else:
            src.unlink(missing_ok=True)
        self.progress.emit(self.jid, 100, 0.0, "")
        self._log(f"GIF creado: {dst.name} ({M.fmt_size(dst.stat().st_size)})")
        self.file_done.emit(self.jid, str(dst))
        return dst


# ───────────────────────── cola ─────────────────────────
NO_RETRY = re.compile(r"unsupported url|not a valid url|private video|video unavailable|"
                      r"no se encontró|is not available|404", re.I)


@dataclass
class Job:
    id: int
    url: str
    cfg: dict
    title: str = ""
    state: str = "queued"      # queued · running · processing · done · error · cancelled
    status: str = "En cola"
    pct: float = 0.0
    bps: float = 0.0
    eta: str = ""
    files: list = field(default_factory=list)
    log: list = field(default_factory=list)
    error: str = ""
    attempts: int = 0

    @property
    def active(self):
        return self.state in ("running", "processing")

    @property
    def finished(self):
        return self.state in ("done", "error", "cancelled")


class JobManager(QObject):
    """Cola con varias descargas simultáneas y reintentos automáticos."""
    added = Signal(int)
    changed = Signal(int)
    removed = Signal(int)
    idle = Signal(int, int, str)   # completadas, con error, último archivo (fin de una tanda)
    log = Signal(str)

    def __init__(self, parallel=2, retries=1):
        super().__init__()
        self.jobs: dict[int, Job] = {}
        self.pending = deque()
        self.threads: dict[int, DownloadThread] = {}
        self.waiting_retry = set()
        self.parallel, self.retries = parallel, retries
        self._next = 1
        self._ok = self._err = 0
        self._last_file = ""

    # ── consultas ──
    def counts(self):
        active = sum(1 for j in self.jobs.values() if j.active)
        queued = sum(1 for j in self.jobs.values() if j.state == "queued")
        done = sum(1 for j in self.jobs.values() if j.state == "done")
        failed = sum(1 for j in self.jobs.values() if j.state == "error")
        speed = sum(j.bps for j in self.jobs.values() if j.state == "running")
        return active, queued, done, failed, speed

    def busy(self):
        return bool(self.threads or self.pending or self.waiting_retry)

    # ── acciones ──
    def add(self, url, cfg):
        j = Job(self._next, url, cfg, title=url)
        self._next += 1
        self.jobs[j.id] = j
        self.pending.append(j.id)
        self.added.emit(j.id)
        self._pump()
        return j.id

    def set_limits(self, parallel, retries):
        self.parallel, self.retries = max(1, parallel), max(0, retries)
        self._pump()

    def cancel(self, jid):
        j = self.jobs.get(jid)
        if not j:
            return
        if j.state == "queued":
            if jid in self.pending:
                self.pending.remove(jid)
            self.waiting_retry.discard(jid)
            j.state, j.status, j.bps = "cancelled", "Cancelado", 0.0
            self.changed.emit(jid)
            self._check_idle()
        elif j.active and jid in self.threads:
            j.status = "Cancelando…"
            self.threads[jid].cancel()
            self.changed.emit(jid)

    def cancel_all(self):
        for jid in list(self.jobs):
            self.cancel(jid)

    def retry(self, jid):
        j = self.jobs.get(jid)
        if not j or not j.finished:
            return
        j.state, j.status, j.pct, j.bps, j.eta, j.error = "queued", "En cola", 0.0, 0.0, "", ""
        j.attempts = 0
        j.files.clear()
        self.pending.append(jid)
        self.changed.emit(jid)
        self._pump()

    def remove(self, jid):
        j = self.jobs.get(jid)
        if not j or j.active:
            return
        self.cancel(jid)
        del self.jobs[jid]
        self.removed.emit(jid)

    def clear_finished(self):
        for jid in [j.id for j in self.jobs.values() if j.finished]:
            del self.jobs[jid]
            self.removed.emit(jid)

    def shutdown(self, wait_ms=3000):
        self.cancel_all()
        for t in list(self.threads.values()):
            t.wait(wait_ms)

    # ── funcionamiento interno ──
    def _pump(self):
        while len(self.threads) < self.parallel and self.pending:
            jid = self.pending.popleft()
            j = self.jobs.get(jid)
            if not j or j.state != "queued":
                continue
            j.attempts += 1
            j.state, j.status, j.pct, j.error = "running", "Iniciando…", -1, ""
            t = DownloadThread(jid, j.url, dict(j.cfg))
            t.progress.connect(self._on_progress)
            t.status.connect(self._on_status)
            t.title.connect(self._on_title)
            t.log.connect(self._on_log)
            t.file_done.connect(self._on_file)
            t.result.connect(self._on_result)
            t.finished.connect(lambda jid=jid: self._on_thread_end(jid))
            self.threads[jid] = t
            t.start()
            self.changed.emit(jid)

    def _on_progress(self, jid, pct, bps, eta):
        j = self.jobs.get(jid)
        if j and j.active:
            j.pct, j.bps, j.eta = pct, bps, eta
            self.changed.emit(jid)

    def _on_status(self, jid, phase, text):
        j = self.jobs.get(jid)
        if j and j.active and j.status != "Cancelando…":
            j.state, j.status = phase, text
            if phase == "processing":
                j.bps = 0.0
            self.changed.emit(jid)

    def _on_title(self, jid, title):
        j = self.jobs.get(jid)
        if j and title and j.title != title:
            j.title = title
            self.changed.emit(jid)

    def _on_log(self, jid, msg):
        j = self.jobs.get(jid)
        if j:
            j.log.append(msg)
        self.log.emit(f"[{jid}] {msg}")

    def _on_file(self, jid, path):
        j = self.jobs.get(jid)
        if j and path not in j.files:
            j.files.append(path)
            self._last_file = path

    def _on_result(self, jid, outcome, msg):
        j = self.jobs.get(jid)
        if not j:
            return
        j.bps = 0.0
        if outcome == "error" and j.attempts <= self.retries and not NO_RETRY.search(msg):
            j.state, j.status = "queued", f"Reintentando ({j.attempts}/{self.retries})…"
            j.error = msg
            self.waiting_retry.add(jid)
            self._on_log(jid, f"↻ Nuevo intento en 2 s ({j.attempts}/{self.retries})")
            QTimer.singleShot(2000, lambda: self._requeue(jid))
        elif outcome == "done":
            j.state, j.status, j.pct, j.eta = "done", "Completado", 100, ""
            self._ok += 1
        elif outcome == "cancelled":
            j.state, j.status, j.eta = "cancelled", "Cancelado", ""
        else:
            j.state, j.status, j.error, j.eta = "error", msg.splitlines()[0] if msg else "Error", msg, ""
            self._err += 1
        self.changed.emit(jid)

    def _requeue(self, jid):
        if jid not in self.waiting_retry:
            return
        self.waiting_retry.discard(jid)
        j = self.jobs.get(jid)
        if j and j.state == "queued":
            self.pending.appendleft(jid)
            self._pump()
        self._check_idle()

    def _on_thread_end(self, jid):
        t = self.threads.pop(jid, None)
        if t:
            t.deleteLater()
        self._pump()
        self._check_idle()

    def _check_idle(self):
        if not self.busy() and (self._ok or self._err):
            self.idle.emit(self._ok, self._err, self._last_file)
            self._ok = self._err = 0
