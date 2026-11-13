"""Ventana principal «Barra de mando»: enlace arriba, receta de formato, cola debajo."""
import ctypes
import re
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter,
    QStackedWidget, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)
from yt_dlp.version import __version__ as YTDLP_VERSION

from . import media as M
from .dialogs import (AudioEditorDialog, CutterDialog, LogDialog, SettingsDialog, open_file,
                      reveal)
from .engine import DEFAULT_TEMPLATE, InfoWorker, JobManager, UpdateWorker, describe_cfg
from .paths import APP_DIR, FROZEN, RES_DIR
from .theme import C, icon, stylesheet
from .widgets import (DropOverlay, ElidedLabel, EmptyState, GainControl, JobRow,
                      ProgressLine, Segmented, Thumb, hsep, label, tool_button, vsep)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def make_settings():
    """En el .exe (o si existe ajustes.ini junto al programa) los ajustes se guardan en
    ajustes.ini al lado del ejecutable; si no, en el registro de Windows."""
    ini = APP_DIR / "ajustes.ini"
    if FROZEN or ini.exists():
        return QSettings(str(ini), QSettings.IniFormat)
    s = QSettings("Fluxio", "Fluxio")
    if not s.allKeys():   # primera vez con el nombre nuevo: se copian los ajustes antiguos
        old = QSettings("VideoDownloader", "App")
        for k in old.allKeys():
            s.setValue(k, old.value(k))
    return s


class UrlBar(QLineEdit):
    """Campo de enlaces: al pegar convierte los saltos de línea en espacios."""

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Paste):
            text = QGuiApplication.clipboard().text()
            if text:
                self.insert(" ".join(text.split()))
            return
        super().keyPressEvent(e)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fluxio")
        self.setMinimumSize(720, 540)
        self.settings = make_settings()
        self.prefs = self._load_prefs()
        self.ffmpeg = M.find_ffmpeg(self.prefs["ffmpeg_custom"])
        self.manager = JobManager(self.prefs["parallel"], self.prefs["retries"])
        self.rows = {}
        self.dialogs = []
        self.tray = None
        self._info_workers = set()
        self._analyzing = None
        self.analyzed_url = None
        self.analyzed_info = None
        self._last_clip = ""
        self._clip_urls = []
        self._toast_on = False
        self._ff_warned = False

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_header())
        lay.addWidget(self._build_body(), 1)
        lay.addWidget(self._build_statusbar())

        self.overlay = DropOverlay(root)
        self.overlay.dropped.connect(self._handle_drop)
        self.setAcceptDrops(True)

        self._load_options()
        self._connect_options()
        self._sync_mode()

        m = self.manager
        m.added.connect(self._on_added)
        m.changed.connect(self._on_changed)
        m.removed.connect(self._on_removed)
        m.idle.connect(self._on_idle)
        m.log.connect(self.log.appendPlainText)
        self._status_timer = QTimer(self, singleShot=True, interval=150,
                                    timeout=self._refresh_status)
        self._tick = QTimer(self, interval=1000, timeout=self._schedule_status)
        self._analyze_timer = QTimer(self, singleShot=True, interval=450,
                                     timeout=self._maybe_analyze)

        for keys, fn in (("Ctrl+L", self._focus_url), ("Ctrl+,", self.open_settings),
                         ("Ctrl+V", self._paste_anywhere), ("Escape", self._escape)):
            QShortcut(QKeySequence(keys), self, fn)
        QGuiApplication.instance().applicationStateChanged.connect(self._on_app_state)

        self.apply_prefs(save=False)
        self._refresh_status()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(960, 720)
        self.url.setFocus()

    # ───────────────────────── construcción ─────────────────────────
    def _build_header(self):
        header = QFrame()
        header.setObjectName("header")
        v = QVBoxLayout(header)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.url = UrlBar()
        self.url.setObjectName("urlbar")
        self.url.setPlaceholderText("Pega uno o varios enlaces de vídeo, audio o GIF")
        self.url.setAccessibleName("Enlaces para descargar")
        self.url.setClearButtonEnabled(True)
        self.url.setAcceptDrops(False)   # los arrastres los gestiona la ventana
        self.url.addAction(icon("link", C["text3"], 16), QLineEdit.LeadingPosition)
        self.url.returnPressed.connect(self.add_downloads)
        self.url.textChanged.connect(self._on_url_text)
        self.btn_dl = QPushButton(" Descargar")
        self.btn_dl.setObjectName("primary")
        self.btn_dl.setIcon(icon("download", C["on_accent"], 16))
        self.btn_dl.setMinimumHeight(38)
        self.btn_dl.setCursor(Qt.PointingHandCursor)
        self.btn_dl.clicked.connect(self.add_downloads)

        self.btn_tools = tool_button("tools", "Herramientas", size=18)
        self.btn_tools.setPopupMode(QToolButton.InstantPopup)
        tm = QMenu(self.btn_tools)
        tm.addAction(icon("cut"), "Recortar un vídeo o audio…").triggered.connect(
            lambda: self.open_cutter())
        tm.addAction(icon("volume"), "Editor de volumen…").triggered.connect(
            lambda: self.open_audio_editor())
        tm.addSeparator()
        tm.addAction(icon("folder"), "Abrir carpeta de descargas").triggered.connect(
            self.open_folder)
        tm.addAction(icon("retry"), "Actualizar yt-dlp").triggered.connect(
            lambda: self.update_ytdlp())
        self.btn_tools.setMenu(tm)
        self.btn_settings = tool_button("settings", "Ajustes (Ctrl+,)", self.open_settings, 18)
        r1.addWidget(self.url, 1)
        r1.addWidget(self.btn_dl)
        r1.addSpacing(4)
        r1.addWidget(self.btn_tools)
        r1.addWidget(self.btn_settings)
        v.addLayout(r1)

        # aviso de enlace copiado
        self.clip = QFrame()
        self.clip.setObjectName("clip")
        cl = QHBoxLayout(self.clip)
        cl.setContentsMargins(10, 4, 4, 4)
        cl.setSpacing(8)
        ic = label()
        ic.setPixmap(icon("paste", C["accent"], 14).pixmap(14, 14))
        self.lbl_clip = ElidedLabel("", mode=Qt.ElideMiddle)
        b_use = QPushButton("Pegar")
        b_use.setObjectName("flat")
        b_use.setStyleSheet(f"color: {C['accent']};")
        b_use.clicked.connect(self._use_clip)
        b_x = tool_button("close", "Descartar", self.clip.hide, 12)
        cl.addWidget(ic)
        cl.addWidget(self.lbl_clip, 1)
        cl.addWidget(b_use)
        cl.addWidget(b_x)
        self.clip.hide()
        v.addWidget(self.clip)

        r2 = QHBoxLayout()
        r2.setSpacing(10)
        self.seg = Segmented([("video", "Vídeo", "video"), ("audio", "Audio", "music"),
                              ("gif", "GIF", "image")])
        self.btn_recipe = QPushButton()
        self.btn_recipe.setObjectName("chip")
        self.btn_recipe.setCheckable(True)
        self.btn_recipe.setLayoutDirection(Qt.RightToLeft)
        self.btn_recipe.setIcon(icon("chevdown", C["text2"], 10))
        self.btn_recipe.setIconSize(QSize(10, 10))
        self.btn_recipe.setCursor(Qt.PointingHandCursor)
        self.btn_recipe.setToolTip("Opciones de formato y extras")
        self.btn_recipe.toggled.connect(self._toggle_options)
        self.btn_recipe.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.btn_recipe.setMinimumWidth(120)
        self._recipe_text = ""
        self.btn_folder = QPushButton()
        self.btn_folder.setObjectName("chip")
        self.btn_folder.setIcon(icon("folder", C["text2"], 14))
        self.btn_folder.setCursor(Qt.PointingHandCursor)
        fm = QMenu(self.btn_folder)
        fm.addAction(icon("folder"), "Abrir carpeta").triggered.connect(self.open_folder)
        fm.addAction(icon("open"), "Cambiar carpeta…").triggered.connect(self._choose_dir)
        # el menú se abre a mano: con setMenu() Qt ignora el relleno y pega el icono al borde
        self._folder_menu = fm
        self.btn_folder.setStyleSheet("QPushButton#chip { text-align: center; }")
        self.btn_folder.clicked.connect(
            lambda: fm.exec(self.btn_folder.mapToGlobal(self.btn_folder.rect().bottomLeft())))
        r2.addWidget(self.seg)
        r2.addWidget(self.btn_recipe, 1)
        r2.addWidget(self.btn_folder)
        v.addLayout(r2)

        v.addWidget(self._build_options())
        v.addWidget(self._build_preview())
        return header

    def _build_options(self):
        self.options = QFrame()
        self.options.setObjectName("panel")
        pv = QVBoxLayout(self.options)
        pv.setContentsMargins(16, 14, 16, 14)
        pv.setSpacing(12)

        def field(text, w, width=170):
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(label(text, "faint"))
            w.setFixedWidth(width)
            box.addWidget(w)
            return box

        def combo(items):
            c = QComboBox()
            for text, data in items:
                c.addItem(text, data)
            return c

        self.cmb_quality = combo([("Mejor disponible", None), ("2160p (4K)", 2160),
                                  ("1440p", 1440), ("1080p", 1080), ("720p", 720),
                                  ("480p", 480), ("360p", 360)])
        self.cmb_vfmt = combo([(x, x) for x in ("mp4", "mkv", "webm")])
        self.cmb_afmt = combo([(x, x) for x in ("mp3", "m4a", "opus", "flac", "wav")])
        self.cmb_abr = combo([(f"{k} kbps", k) for k in (320, 256, 192, 128)])
        self.cmb_abr.setToolTip("Solo afecta a mp3, m4a y opus")
        self.cmb_gwidth = combo([("Automático", None), ("320 px", 320), ("480 px", 480),
                                 ("640 px", 640), ("800 px", 800)])
        self.cmb_gwidth.setToolTip(f"Automático: los GIF se guardan tal cual y los vídeos se "
                                   f"reducen a {M.GIF_AUTO_WIDTH} px de ancho como máximo.")
        self.cmb_gfps = combo([("Automático", None), ("10 fps", 10), ("12 fps", 12),
                               ("15 fps", 15), ("20 fps", 20), ("24 fps", 24)])
        self.cmb_gfps.setToolTip(f"Automático: los GIF se guardan tal cual y los vídeos se "
                                 f"limitan a {M.GIF_AUTO_FPS} fotogramas por segundo.")

        self.fmt_stack = QStackedWidget()
        for a, wa, b, wb in (("Calidad", self.cmb_quality, "Contenedor", self.cmb_vfmt),
                             ("Formato", self.cmb_afmt, "Calidad", self.cmb_abr),
                             ("Ancho", self.cmb_gwidth, "Fotogramas", self.cmb_gfps)):
            page = QWidget()
            page.setObjectName("transparent")
            h = QHBoxLayout(page)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(12)
            h.addLayout(field(a, wa))
            h.addLayout(field(b, wb, 130))
            h.addStretch(1)
            self.fmt_stack.addWidget(page)
        top = QHBoxLayout()
        top.addWidget(self.fmt_stack, 1)
        self.lbl_gif_hint = label("Enlaces a GIF (Giphy, Tenor, Imgur…) se guardan tal cual. "
                                  "Los vídeos (X, Reddit, YouTube…) se convierten a GIF.",
                                  "faint", wrap=True)
        self.lbl_gif_hint.setMaximumWidth(330)
        top.addWidget(self.lbl_gif_hint)
        pv.addLayout(top)
        pv.addWidget(hsep())

        self.chk_subs = QCheckBox("Subtítulos")
        self.chk_meta = QCheckBox("Metadatos y capítulos")
        self.chk_thumb = QCheckBox("Miniatura incrustada")
        self.chk_norm = QCheckBox("Normalizar volumen")
        self.chk_norm.setToolTip("Iguala el volumen a −14 LUFS, el nivel estándar de Spotify. "
                                 "Requiere ffmpeg y tarda un poco más.")
        self.chk_gain = QCheckBox("Ajustar volumen")
        self.chk_gain.setToolTip("Sube o baja el volumen del audio descargado. Con "
                                 "«Normalizar», se aplica sobre el nivel normalizado.")
        self.chk_playlist = QCheckBox("Lista de reproducción completa")
        self.chk_cut = QCheckBox("Descargar solo un fragmento")
        self.checks_grid = QGridLayout()
        self.checks_grid.setHorizontalSpacing(24)
        self.checks_grid.setVerticalSpacing(8)
        pv.addLayout(self.checks_grid)

        def ctx_row(title, *widgets):
            w = QWidget()
            w.setObjectName("transparent")
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            lb = label(title, "faint")
            lb.setFixedWidth(80)
            h.addWidget(lb)
            for x in widgets:
                if x is None:
                    h.addStretch(1)
                elif isinstance(x, int):
                    h.addSpacing(x)
                else:
                    h.addWidget(x)
            return w

        self.edt_langs = QLineEdit("es,en")
        self.edt_langs.setFixedWidth(120)
        self.edt_langs.setAccessibleName("Idiomas de los subtítulos")
        self.edt_langs.setToolTip("Códigos de idioma separados por comas")
        self.chk_embed = QCheckBox("Incrustar en el vídeo")
        self.row_subs = ctx_row("Idiomas", self.edt_langs, 12, self.chk_embed, None)
        self.gain = GainControl()
        self.gain.setMinimumWidth(300)
        self.gain.setMaximumWidth(440)
        self.row_gain = ctx_row("Volumen", self.gain, None)
        self.edt_start = QLineEdit()
        self.edt_start.setObjectName("time")
        self.edt_start.setPlaceholderText("inicio 0:30")
        self.edt_start.setFixedWidth(100)
        self.edt_end = QLineEdit()
        self.edt_end.setObjectName("time")
        self.edt_end.setPlaceholderText("fin 1:45")
        self.edt_end.setFixedWidth(100)
        arrow = label("→", "faint")
        hint = label("¿Prefieres elegirlo viéndolo? Descarga entero y usa Recortar.", "faint")
        self.row_cut = ctx_row("Fragmento", self.edt_start, arrow, self.edt_end, 12, hint, None)
        for w in (self.row_subs, self.row_gain, self.row_cut):
            pv.addWidget(w)
        self.options.hide()
        return self.options

    def _build_preview(self):
        self.preview = QFrame()
        self.preview.setObjectName("panel")
        outer = QVBoxLayout(self.preview)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        h = QHBoxLayout()
        h.setContentsMargins(12, 12, 8, 12)
        h.setSpacing(14)
        self.thumb = Thumb(144, 81)
        h.addWidget(self.thumb, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.lbl_ptitle = ElidedLabel("", "jobtitle")
        self.lbl_pmeta = ElidedLabel("", "muted")
        col.addWidget(self.lbl_ptitle)
        col.addWidget(self.lbl_pmeta)
        col.addStretch(1)
        self.fmt_row = QWidget()
        self.fmt_row.setObjectName("transparent")
        fr = QHBoxLayout(self.fmt_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.setSpacing(8)
        fr.addWidget(label("Formato exacto", "faint"))
        self.cmb_format = QComboBox()
        self.cmb_format.setMinimumWidth(260)
        self.cmb_format.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_format.setAccessibleName("Formato exacto")
        self.cmb_format.currentIndexChanged.connect(lambda *_: self._update_recipe())
        fr.addWidget(self.cmb_format, 1)
        fr.addStretch(0)
        col.addWidget(self.fmt_row)
        h.addLayout(col, 1)
        h.addWidget(tool_button("close", "Ocultar vista previa", self._dismiss_preview, 12),
                    0, Qt.AlignTop)
        outer.addLayout(h)
        self.pbar = ProgressLine(2)
        outer.addWidget(self.pbar)
        self.preview.hide()
        return self.preview

    def _build_body(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(20, 12, 14, 6)
        self.lbl_queue = label("DESCARGAS", "section")
        self.btn_clear = QPushButton("Limpiar terminadas")
        self.btn_clear.setObjectName("flat")
        self.btn_clear.clicked.connect(self.manager.clear_finished)
        self.btn_cancel_all = QPushButton("Cancelar todo")
        self.btn_cancel_all.setObjectName("flat")
        self.btn_cancel_all.clicked.connect(self.manager.cancel_all)
        bar.addWidget(self.lbl_queue)
        bar.addStretch(1)
        bar.addWidget(self.btn_cancel_all)
        bar.addWidget(self.btn_clear)
        v.addLayout(bar)

        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        self.queue_stack = QStackedWidget()
        self.queue_stack.addWidget(EmptyState())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("transparent")
        self.list_lay = QVBoxLayout(holder)
        self.list_lay.setContentsMargins(4, 0, 4, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        scroll.setWidget(holder)
        scroll.viewport().setObjectName("transparent")
        self.queue_stack.addWidget(scroll)
        self.split.addWidget(self.queue_stack)

        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("Aquí aparecen los mensajes de yt-dlp y ffmpeg.")
        self.log.hide()
        self.split.addWidget(self.log)
        self.split.setStretchFactor(0, 1)
        v.addWidget(self.split, 1)
        return body

    def _build_statusbar(self):
        sb = QFrame()
        sb.setObjectName("statusbar")
        h = QHBoxLayout(sb)
        h.setContentsMargins(16, 4, 10, 4)
        h.setSpacing(10)
        self.btn_ff = QPushButton()
        self.btn_ff.setObjectName("flat")
        self.btn_ff.setCursor(Qt.PointingHandCursor)
        self.btn_ff.clicked.connect(self.open_settings)
        self.btn_ff.setStyleSheet("font-weight: 400; padding: 2px 6px;")
        self.lbl_yt = label(f"yt-dlp {YTDLP_VERSION}", "faint")
        self.lbl_activity = label("", "faint")
        self.btn_log = QPushButton(" Registro")
        self.btn_log.setObjectName("flat")
        self.btn_log.setCheckable(True)
        self.btn_log.setIcon(icon("log", C["text2"], 14))
        self.btn_log.setStyleSheet("font-weight: 400; padding: 2px 8px;")
        self.btn_log.toggled.connect(self._toggle_log)
        h.addWidget(self.btn_ff)
        h.addWidget(vsep(12))
        h.addWidget(self.lbl_yt)
        h.addStretch(1)
        h.addWidget(self.lbl_activity)
        h.addWidget(vsep(12))
        h.addWidget(self.btn_log)
        return sb

    # ───────────────────────── preferencias ─────────────────────────
    def _load_prefs(self):
        s = self.settings

        def b(k, d):
            v = s.value(k, d)
            return v if isinstance(v, bool) else str(v).lower() in ("true", "1")

        def i(k, d):
            try:
                return int(s.value(k, d))
            except (TypeError, ValueError):
                return d

        return dict(
            outdir=s.value("outdir", str(Path.home() / "Downloads")),
            template=s.value("template", DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE,
            parallel=max(1, i("parallel", 2)), retries=max(0, i("retries", 1)),
            cookies=s.value("cookies", "") or None, ffhls=b("ffhls", True),
            clipboard=b("clipboard", True), notify=b("notify", True),
            ffmpeg_custom=s.value("ffmpeg", "") or "",
        )

    def apply_prefs(self, save=True):
        p = self.prefs
        if save:
            for k in ("outdir", "template", "parallel", "retries", "ffhls", "clipboard",
                      "notify"):
                self.settings.setValue(k, p[k])
            self.settings.setValue("cookies", p["cookies"] or "")
            self.settings.setValue("ffmpeg", p["ffmpeg_custom"])
        self.ffmpeg = M.find_ffmpeg(p["ffmpeg_custom"])
        self.manager.set_limits(p["parallel"], p["retries"])
        name = Path(p["outdir"]).name or p["outdir"]
        self.btn_folder.setText(" " + self.btn_folder.fontMetrics().elidedText(name, Qt.ElideMiddle, 160))
        self.btn_folder.setToolTip(f"Guardar en: {p['outdir']}")
        if self.ffmpeg:
            self.btn_ff.setText("●  ffmpeg listo")
            self.btn_ff.setToolTip(self.ffmpeg)
            col = C["ok"]
        else:
            self.btn_ff.setText("●  ffmpeg no encontrado · configurar")
            self.btn_ff.setToolTip("Sin ffmpeg no se pueden unir vídeo y audio ni convertir")
            col = C["warn"]
        self.btn_ff.setStyleSheet(f"font-weight: 400; padding: 2px 6px; color: {col};")
        self._ensure_tray()
        self._update_recipe()

    _OPT_COMBOS = ("quality", "vfmt", "afmt", "abr", "gwidth", "gfps")
    _OPT_CHECKS = ("subs", "embed", "meta", "thumb", "norm", "gain", "playlist")

    def _opt_widgets(self):
        return ({"quality": self.cmb_quality, "vfmt": self.cmb_vfmt, "afmt": self.cmb_afmt,
                 "abr": self.cmb_abr, "gwidth": self.cmb_gwidth, "gfps": self.cmb_gfps},
                {"subs": self.chk_subs, "embed": self.chk_embed, "meta": self.chk_meta,
                 "thumb": self.chk_thumb, "norm": self.chk_norm, "gain": self.chk_gain,
                 "playlist": self.chk_playlist})

    def _load_options(self):
        s = self.settings
        combos, checks = self._opt_widgets()
        defaults = {"meta": True, "embed": False, "thumb": False,
                    "norm": str(s.value("norm", "false")).lower() == "true"}
        for k, w in combos.items():
            try:
                w.setCurrentIndex(min(w.count() - 1, max(0, int(s.value(f"opt/{k}", 0)))))
            except (TypeError, ValueError):
                pass
        for k, w in checks.items():
            v = s.value(f"opt/{k}", defaults.get(k, False))
            w.setChecked(v if isinstance(v, bool) else str(v).lower() in ("true", "1"))
        self.edt_langs.setText(s.value("opt/langs", "es,en") or "es,en")
        try:
            self.gain.setValue(float(s.value("opt/gain_db", 0)))
        except (TypeError, ValueError):
            pass
        self.seg.setValue(s.value("opt/mode", "video"))

    def _save_options(self):
        s = self.settings
        combos, checks = self._opt_widgets()
        for k, w in combos.items():
            s.setValue(f"opt/{k}", w.currentIndex())
        for k, w in checks.items():
            s.setValue(f"opt/{k}", w.isChecked())
        s.setValue("opt/langs", self.edt_langs.text())
        s.setValue("opt/gain_db", self.gain.value())
        s.setValue("opt/mode", self.seg.value())

    def _connect_options(self):
        combos, checks = self._opt_widgets()
        for w in combos.values():
            w.currentIndexChanged.connect(self._update_recipe)
        for w in (*checks.values(), self.chk_cut):
            w.toggled.connect(self._sync_context)
        for w in (self.edt_langs, self.edt_start, self.edt_end):
            w.textChanged.connect(self._update_recipe)
        self.gain.changed.connect(self._update_recipe)
        self.seg.changed.connect(self._sync_mode)

    # ───────────────────────── opciones visibles ─────────────────────────
    def mode(self):
        return self.seg.value()

    def _sync_mode(self, *_):
        m = self.mode()
        self.fmt_stack.setCurrentIndex({"video": 0, "audio": 1, "gif": 2}[m])
        self.lbl_gif_hint.setVisible(m == "gif")
        checks = {"video": [self.chk_subs, self.chk_meta, self.chk_thumb, self.chk_playlist,
                            self.chk_cut],
                  "audio": [self.chk_norm, self.chk_gain, self.chk_meta, self.chk_thumb,
                            self.chk_playlist, self.chk_cut],
                  "gif": [self.chk_playlist, self.chk_cut]}[m]
        g = self.checks_grid
        for w in (self.chk_subs, self.chk_meta, self.chk_thumb, self.chk_norm, self.chk_gain,
                  self.chk_playlist, self.chk_cut):
            g.removeWidget(w)
            w.setVisible(w in checks)
        for i, w in enumerate(checks):
            g.addWidget(w, i // 3, i % 3)
        self.chk_thumb.setText("Portada incrustada" if m == "audio" else "Miniatura incrustada")
        self._fill_formats()
        self._sync_context()

    def _sync_context(self, *_):
        m = self.mode()
        self.row_subs.setVisible(m == "video" and self.chk_subs.isChecked())
        self.row_gain.setVisible(m == "audio" and self.chk_gain.isChecked())
        self.row_cut.setVisible(self.chk_cut.isChecked())
        self._update_recipe()

    def _toggle_options(self, on):
        self.options.setVisible(on)
        self.btn_recipe.setIcon(icon("chevup" if on else "chevdown", C["text2"], 10))

    def _update_recipe(self, *_):
        if not hasattr(self, "btn_recipe"):
            return
        try:
            text = describe_cfg(self._format_cfg(self.collect_cfg()), long=True)
        except Exception:
            text = "Opciones"
        self._recipe_text = text
        self._elide_recipe()

    def _elide_recipe(self):
        w = max(40, self.btn_recipe.width() - 52)
        self.btn_recipe.setText(self.btn_recipe.fontMetrics().elidedText(
            self._recipe_text, Qt.ElideRight, w))
        self.btn_recipe.setToolTip(self._recipe_text)

    def collect_cfg(self):
        p = self.prefs
        mode = self.mode()
        return dict(
            mode=mode,
            gif_width=self.cmb_gwidth.currentData(), gif_fps=self.cmb_gfps.currentData(),
            gain=self.gain.value() if mode == "audio" and self.chk_gain.isChecked() else 0.0,
            height=self.cmb_quality.currentData(),
            vfmt=self.cmb_vfmt.currentData(), afmt=self.cmb_afmt.currentData(),
            abr=self.cmb_abr.currentData(), playlist=self.chk_playlist.isChecked(),
            ffhls=p["ffhls"], meta=self.chk_meta.isChecked(), thumb=self.chk_thumb.isChecked(),
            subs=mode == "video" and self.chk_subs.isChecked(),
            sublangs=self.edt_langs.text(), embed_subs=self.chk_embed.isChecked(),
            cut=self.chk_cut.isChecked(), start=self.edt_start.text().strip(),
            end=self.edt_end.text().strip(),
            norm=mode == "audio" and self.chk_norm.isChecked(),
            outdir=p["outdir"], ffmpeg=self.ffmpeg, cookies=p["cookies"],
            template=p["template"],
        )

    def _format_cfg(self, cfg, url=None):
        """Añade el formato exacto elegido en la vista previa (solo para su enlace)."""
        f = self.cmb_format.currentData()
        if f and cfg["mode"] != "gif" and self.analyzed_url and not self.preview.isHidden() \
                and (url is None or url == self.analyzed_url):
            cfg.update(format_id=f["id"], format_has_audio=f["audio"], format_label=f["short"])
        return cfg

    # ───────────────────────── enlaces y análisis ─────────────────────────
    def _urls(self):
        return [u for u in self.url.text().split() if u]

    def _on_url_text(self, text):
        n = len(self._urls())
        self.btn_dl.setText(f" Descargar {n}" if n > 1 else " Descargar")
        if self.clip.isVisible() and any(u in text for u in self._clip_urls):
            self.clip.hide()
        self._analyze_timer.start()

    def _maybe_analyze(self):
        urls = self._urls()
        if len(urls) != 1 or not URL_RE.match(urls[0]):
            self.analyzed_url = None
            self._analyzing = None
            self.preview.hide()
            self._update_recipe()
            return
        url = urls[0]
        if url in (self.analyzed_url, self._analyzing):
            return
        self._analyzing = url
        self.analyzed_url = None
        self.analyzed_info = None
        self.thumb.setPixmap(None)
        self.thumb.setGlyph({"video": "video", "audio": "music", "gif": "image"}[self.mode()])
        self.lbl_ptitle.setText("Analizando enlace…")
        self.lbl_pmeta.setText(url)
        self.fmt_row.hide()
        self.pbar.setBusy(True)
        self.pbar.show()
        self.preview.show()
        w = InfoWorker(url, self.prefs["cookies"])
        w.done.connect(self._info_done)
        w.failed.connect(self._info_failed)
        w.finished.connect(lambda w=w: (self._info_workers.discard(w), w.deleteLater()))
        self._info_workers.add(w)
        w.start()

    def _current_single(self):
        urls = self._urls()
        return urls[0] if len(urls) == 1 else None

    def _info_done(self, url, info, thumb):
        if url != self._analyzing or url != self._current_single():
            return
        self._analyzing = None
        self.analyzed_url = url
        self.analyzed_info = info
        self.pbar.setBusy(False)
        self.pbar.hide()
        title = info.get("title") or "Sin título"
        self.lbl_ptitle.setText(title)
        self.lbl_ptitle.setToolTip(title)
        self.lbl_pmeta.setToolTip("")
        bits = [info.get("uploader") or info.get("channel") or "", M.fmt_dur(info.get("duration")),
                info.get("extractor_key") or ""]
        if info.get("playlist_count"):
            bits.append(f"lista de {info['playlist_count']}")
        self.lbl_pmeta.setText("  ·  ".join(b for b in bits if b))
        pm = QPixmap()
        if thumb and pm.loadFromData(thumb):
            self.thumb.setPixmap(pm)
        self._fill_formats()

    def _info_failed(self, url, msg):
        if url != self._analyzing:
            return
        self._analyzing = None
        self.analyzed_url = None
        self.pbar.setBusy(False)
        self.pbar.hide()
        self.lbl_ptitle.setText("Sin vista previa")
        self.lbl_pmeta.setText("No se pudo analizar el enlace; se intentará igualmente al "
                               "descargar.")
        self.lbl_pmeta.setToolTip(msg[:600])
        self.fmt_row.hide()

    def _fill_formats(self):
        info = self.analyzed_info if self.analyzed_url else None
        m = self.mode()
        self.cmb_format.blockSignals(True)
        self.cmb_format.clear()
        self.cmb_format.addItem("Automático (según las opciones)", None)
        lst = []
        if info and m != "gif":
            lst = info["video_formats"] if m == "video" else info["audio_formats"]
            for f in lst:
                self.cmb_format.addItem(f["label"], f)
        self.cmb_format.setCurrentIndex(0)   # al cambiar de modo se vuelve a «Automático»
        self.cmb_format.blockSignals(False)
        self.fmt_row.setVisible(bool(lst))
        self._update_recipe()

    def _dismiss_preview(self):
        self.preview.hide()
        self._update_recipe()

    # ───────────────────────── portapapeles y arrastre ─────────────────────────
    def _on_app_state(self, state):
        if state == Qt.ApplicationActive and self.prefs["clipboard"]:
            QTimer.singleShot(120, self._check_clipboard)

    def _check_clipboard(self):
        text = (QGuiApplication.clipboard().text() or "").strip()
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        known = {j.url for j in self.manager.jobs.values()}
        bar = self.url.text()
        urls = [u for u in dict.fromkeys(URL_RE.findall(text)) if u not in known and u not in bar]
        if not urls:
            return
        self._clip_urls = urls
        self.lbl_clip.setText(f"Enlace copiado: {urls[0]}" if len(urls) == 1 else
                              f"{len(urls)} enlaces copiados: {urls[0]} …")
        self.lbl_clip.setToolTip("\n".join(urls[:20]))
        self.clip.show()

    def _use_clip(self):
        self._append_urls(self._clip_urls)
        self.clip.hide()

    def _append_urls(self, urls):
        cur = self._urls()
        new = [u for u in urls if u not in cur]
        if new:
            self.url.setText(" ".join(cur + new))
        self.url.setFocus()
        self.url.end(False)

    def _paste_anywhere(self):
        self.url.setFocus()
        text = QGuiApplication.clipboard().text()
        if text:
            self.url.insert((" " if self.url.text() and not self.url.text().endswith(" ") else "")
                            + " ".join(text.split()))

    def _focus_url(self):
        self.url.setFocus()
        self.url.selectAll()

    def _escape(self):
        if self.btn_recipe.isChecked():
            self.btn_recipe.setChecked(False)
        elif self.clip.isVisible():
            self.clip.hide()

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasText():
            self.overlay.setGeometry(self.centralWidget().rect())
            self.overlay.raise_()
            self.overlay.show()
            e.acceptProposedAction()

    def _handle_drop(self, md):
        web, files = [], []
        for u in md.urls() if md.hasUrls() else []:
            (files if u.isLocalFile() else web).append(u.toLocalFile() if u.isLocalFile()
                                                       else u.toString())
        if md.hasText():
            web += [u for u in URL_RE.findall(md.text()) if u not in web]
        if web:
            self._append_urls(web)
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in M.AUDIO_EXTS and ext != ".webm":
                self.open_audio_editor(f)
            elif ext in M.VIDEO_EXTS:
                self.open_cutter(f)
        if not web and not files:
            self.toast("No se encontró ningún enlace en lo que has soltado.", "warn")

    # ───────────────────────── descargas ─────────────────────────
    def add_downloads(self):
        urls = self._urls()
        if not urls:
            self.url.setFocus()
            self.toast("Pega primero un enlace.", "warn")
            return
        cfg = self.collect_cfg()
        try:
            if cfg["cut"]:
                a, b = M.parse_time(cfg["start"]), M.parse_time(cfg["end"])
                if a is not None and b is not None and b <= a:
                    raise ValueError
        except ValueError:
            self.btn_recipe.setChecked(True)
            self.edt_start.setFocus()
            self.toast("Fragmento no válido: usa 90, 1:30 o 1:02:03 y que el fin sea "
                       "posterior al inicio.", "err")
            return
        try:
            Path(cfg["outdir"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.toast(f"No se puede usar la carpeta de destino: {e}", "err")
            return
        if not self.ffmpeg and not self._ff_warned:
            self._ff_warned = True
            self.toast("Sin ffmpeg algunos formatos no se podrán unir ni convertir.", "warn")
        for u in urls:
            self.manager.add(u, self._format_cfg(dict(cfg), u))
        self._save_options()
        self.url.clear()
        self.preview.hide()
        self.analyzed_url = None

    def _on_added(self, jid):
        job = self.manager.jobs[jid]
        row = JobRow(job)
        row.action.connect(self._row_action)
        self.rows[jid] = row
        self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self._schedule_status()

    def _on_changed(self, jid):
        row, job = self.rows.get(jid), self.manager.jobs.get(jid)
        if row and job:
            row.update_job(job)
        self._schedule_status()

    def _on_removed(self, jid):
        row = self.rows.pop(jid, None)
        if row:
            row.setParent(None)
            row.deleteLater()
        self._schedule_status()

    def _row_action(self, jid, act):
        job = self.manager.jobs.get(jid)
        if not job:
            return
        files = [f for f in job.files if Path(f).exists()]
        last = files[-1] if files else None
        if act == "open" and last:
            open_file(last)
        elif act == "folder" and last:
            reveal(last)
        elif act == "cut" and last:
            self.open_cutter(last)
        elif act == "volume" and last:
            self.open_audio_editor(last)
        elif act == "retry":
            self.manager.retry(jid)
        elif act == "cancel":
            self.manager.cancel(jid)
        elif act == "remove":
            self.manager.remove(jid)
        elif act == "copy":
            QGuiApplication.clipboard().setText(job.url)
            self._last_clip = job.url   # que no se proponga de nuevo
            self.toast("Enlace copiado.")
        elif act == "browser":
            QDesktopServices.openUrl(QUrl(job.url))
        elif act == "log":
            LogDialog(self, job.title or job.url, job.log).exec()

    def _on_idle(self, ok, err, last):
        QApplication.alert(self)
        if not (self.prefs["notify"] and self.tray) or self.isActiveWindow():
            return
        if ok == 1 and not err:
            title, body = "Descarga completada", Path(last).name if last else ""
        else:
            title = "Descargas terminadas"
            body = f"{ok} completada{'s' if ok != 1 else ''}"
            if err:
                body += f" · {err} con error"
        kind = QSystemTrayIcon.Warning if err and not ok else QSystemTrayIcon.Information
        self.tray.showMessage(title, body, kind, 6000)

    def _schedule_status(self):
        if not self._status_timer.isActive():
            self._status_timer.start()

    def _refresh_status(self):
        active, queued, done, failed, speed = self.manager.counts()
        total = len(self.manager.jobs)
        self.queue_stack.setCurrentIndex(1 if total else 0)
        self.lbl_queue.setText(f"DESCARGAS · {total}" if total else "DESCARGAS")
        self.btn_cancel_all.setVisible(bool(active or queued))
        self.btn_clear.setVisible(any(j.finished for j in self.manager.jobs.values()))
        if self._toast_on:
            return
        bits = []
        if active:
            bits.append(f"{active} descargando")
        if queued:
            bits.append(f"{queued} en cola")
        if speed:
            bits.append(M.fmt_speed(speed))
        if not bits:
            if done:
                bits.append(f"{done} completada{'s' if done != 1 else ''}")
            if failed:
                bits.append(f"{failed} con error")
        self.lbl_activity.setText("  ·  ".join(bits) or "Listo")
        self.lbl_activity.setStyleSheet("")
        # la velocidad cambia aunque no lleguen cambios de estado
        if active and not self._tick.isActive():
            self._tick.start()
        elif not active:
            self._tick.stop()

    def toast(self, text, kind="muted"):
        """Mensaje breve en la barra de estado."""
        self._toast_on = True
        self.lbl_activity.setText(text)
        self.lbl_activity.setStyleSheet(f"color: {C.get(kind, C['text2'])};")
        QTimer.singleShot(5000, self._end_toast)

    def _end_toast(self):
        self._toast_on = False
        self._refresh_status()

    # ───────────────────────── herramientas ─────────────────────────
    def _show_dialog(self, dlg):
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        self.dialogs.append(dlg)
        dlg.destroyed.connect(lambda *_, d=dlg: self.dialogs.remove(d) if d in self.dialogs else None)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_cutter(self, path=None):
        try:
            dlg = CutterDialog(self, path)
        except ImportError:
            QMessageBox.warning(self, "Recortar", "Falta el módulo QtMultimedia de PySide6.")
            return
        self._show_dialog(dlg)

    def open_audio_editor(self, path=None):
        self._show_dialog(AudioEditorDialog(self, path))

    def recent_audio(self):
        out = []
        for j in reversed(list(self.manager.jobs.values())):
            out += [f for f in j.files if Path(f).suffix.lower() in M.AUDIO_EXTS
                    and Path(f).exists()]
        return list(dict.fromkeys(reversed(out)))

    def file_created(self, path):
        self.log.appendPlainText(f"Archivo creado: {path}")

    def open_settings(self):
        SettingsDialog(self).exec()

    def open_folder(self):
        Path(self.prefs["outdir"]).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.prefs["outdir"]))

    def _choose_dir(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.prefs["outdir"])
        if d:
            self.prefs["outdir"] = d
            self.apply_prefs()

    def _toggle_log(self, on):
        self.log.setVisible(on)
        if on:
            h = self.split.height()
            self.split.setSizes([max(120, h - 200), 200])

    def update_ytdlp(self, callback=None):
        self.toast("Actualizando yt-dlp…")
        w = UpdateWorker()
        self._upd = w

        def done(ok, out):
            self.log.appendPlainText(out)
            if ok:
                QMessageBox.information(self, "yt-dlp", "yt-dlp actualizado. Cierra y vuelve "
                                        "a abrir la app para usar la nueva versión.")
            else:
                self.toast("No se pudo actualizar yt-dlp (detalles en Registro).", "err")
            if callback:
                callback(ok, out)
        w.done.connect(done)
        w.start()

    # ───────────────────────── bandeja del sistema ─────────────────────────
    def _ensure_tray(self):
        want = self.prefs["notify"] and QSystemTrayIcon.isSystemTrayAvailable()
        if want and not self.tray:
            ic = self.windowIcon()
            if ic.isNull():
                ic = icon("download", C["accent"], 32)
            self.tray = QSystemTrayIcon(ic, self)
            self.tray.setToolTip("Fluxio")
            menu = QMenu()
            menu.addAction("Mostrar").triggered.connect(self._show_window)
            menu.addAction("Abrir carpeta de descargas").triggered.connect(self.open_folder)
            menu.addSeparator()
            menu.addAction("Salir").triggered.connect(self.close)
            self.tray.setContextMenu(menu)
            self._tray_menu = menu
            self.tray.activated.connect(
                lambda r: self._show_window() if r == QSystemTrayIcon.Trigger else None)
            self.tray.messageClicked.connect(self._show_window)
        if self.tray:
            self.tray.setVisible(bool(want))

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ───────────────────────── cierre ─────────────────────────
    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._elide_recipe)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.centralWidget().rect())

    def closeEvent(self, e):
        if self.manager.busy():
            r = QMessageBox.question(self, "Salir", "Hay descargas en curso. ¿Cancelarlas y "
                                     "salir?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        self._save_options()
        self.settings.setValue("geometry", self.saveGeometry())
        for d in list(self.dialogs):
            d.close()
        self.manager.shutdown()
        if self.tray:
            self.tray.hide()
        super().closeEvent(e)


def main():
    # para que Windows muestre nuestro icono en la barra de tareas (y no el de Python)
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Fluxio.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Fluxio")
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    icon_path = RES_DIR / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
