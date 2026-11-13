"""Ventanas secundarias: ajustes, editor de volumen, editor de corte y registro."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import media as M
from .engine import (DEFAULT_TEMPLATE, TEMPLATE_TOKENS, CutWorker, FuncWorker,
                     template_example)
from .theme import C, icon
from .widgets import (ElidedLabel, GainControl, ProgressLine, Segmented, Timeline, hsep,
                      label, tool_button)

BROWSERS = [("Ninguna", None), ("Firefox", "firefox"), ("Chrome", "chrome"),
            ("Edge", "edge"), ("Brave", "brave"), ("Opera", "opera")]


def reveal(path):
    """Abre el Explorador con el archivo seleccionado (o la carpeta si no existe)."""
    p = Path(path)
    import subprocess
    import sys
    if sys.platform == "win32" and p.exists():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent if p.is_file() else p)))


def open_file(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _buttons(dialog, ok_text=None, ok_slot=None, cancel_text="Cerrar"):
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = QPushButton(cancel_text)
    cancel.clicked.connect(dialog.reject)
    row.addWidget(cancel)
    ok = None
    if ok_text:
        ok = QPushButton(ok_text)
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(ok_slot)
        row.addWidget(ok)
    return row, ok, cancel


# ───────────────────────── ajustes ─────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        s = win.prefs
        self.setWindowTitle("Ajustes")
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        v.addWidget(label("Ajustes", "h1"))

        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(10)
        g.setColumnMinimumWidth(0, 150)
        g.setColumnStretch(1, 1)
        row = 0

        def section(text):
            nonlocal row
            if row:
                g.addWidget(hsep(), row, 0, 1, 2)
                row += 1
            g.addWidget(label(text.upper(), "section"), row, 0, 1, 2)
            row += 1

        def field(text, w, hint=None):
            nonlocal row
            lb = label(text, "muted")
            lb.setContentsMargins(0, 6, 0, 0)   # alineado con el texto del campo
            g.addWidget(lb, row, 0, Qt.AlignTop | Qt.AlignLeft)
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(w) if isinstance(w, QWidget) else box.addLayout(w)
            if hint:
                box.addWidget(hint)
            g.addLayout(box, row, 1)
            row += 1

        section("Descargas")
        dir_row = QHBoxLayout()
        self.edt_dir = QLineEdit(s["outdir"])
        b = QPushButton("Elegir…")
        b.clicked.connect(self._choose_dir)
        dir_row.addWidget(self.edt_dir, 1)
        dir_row.addWidget(b)
        field("Carpeta de destino", dir_row)

        tpl_row = QHBoxLayout()
        self.edt_tpl = QLineEdit(s["template"])
        self.edt_tpl.setPlaceholderText(DEFAULT_TEMPLATE)
        ins = QPushButton("Insertar ")
        ins.setIcon(icon("chevdown", C["text2"], 10))
        ins.setLayoutDirection(Qt.RightToLeft)
        menu = QMenu(ins)
        names = {"titulo": "Título", "canal": "Canal / autor", "id": "ID del vídeo",
                 "fecha": "Fecha de publicación", "sitio": "Sitio web",
                 "resolucion": "Resolución"}
        for tok in TEMPLATE_TOKENS:
            menu.addAction(f"{names[tok]}   {{{tok}}}").triggered.connect(
                lambda _=False, t=tok: self.edt_tpl.insert("{" + t + "}"))
        menu.addSeparator()
        menu.addAction("Restablecer").triggered.connect(
            lambda: self.edt_tpl.setText(DEFAULT_TEMPLATE))
        ins.setMenu(menu)
        tpl_row.addWidget(self.edt_tpl, 1)
        tpl_row.addWidget(ins)
        self.lbl_example = label("", "faint")
        self.lbl_example.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.edt_tpl.textChanged.connect(self._upd_example)
        field("Nombre de archivo", tpl_row, self.lbl_example)
        self._upd_example()

        self.cmb_parallel = QComboBox()
        for n in (1, 2, 3, 4):
            self.cmb_parallel.addItem(f"{n} a la vez" if n > 1 else "1 (una tras otra)", n)
        self.cmb_parallel.setCurrentIndex(max(0, int(s["parallel"]) - 1))
        field("Descargas simultáneas", self.cmb_parallel)

        self.cmb_retries = QComboBox()
        for n, t in ((0, "No reintentar"), (1, "1 vez"), (2, "2 veces"), (3, "3 veces")):
            self.cmb_retries.addItem(t, n)
        self.cmb_retries.setCurrentIndex(min(3, int(s["retries"])))
        field("Reintentar si falla", self.cmb_retries)

        section("Acceso a los sitios")
        self.cmb_cookies = QComboBox()
        for t, d in BROWSERS:
            self.cmb_cookies.addItem(t, d)
        self.cmb_cookies.setCurrentIndex(max(0, self.cmb_cookies.findData(s["cookies"])))
        field("Cookies del navegador", self.cmb_cookies,
              label("Para contenido con restricción de edad o que exige iniciar sesión. "
                    "Firefox es el más fiable.", "faint", wrap=True))
        self.chk_hls = QCheckBox("Descargar los streams HLS (m3u8) con ffmpeg")
        self.chk_hls.setChecked(s["ffhls"])
        field("Modo compatible", self.chk_hls)

        section("Comodidad")
        self.chk_clip = QCheckBox("Proponer el enlace copiado al volver a la ventana")
        self.chk_clip.setChecked(s["clipboard"])
        field("Portapapeles", self.chk_clip)
        self.chk_notify = QCheckBox("Avisar con una notificación de Windows al terminar")
        self.chk_notify.setChecked(s["notify"])
        field("Notificaciones", self.chk_notify)

        section("Componentes")
        ff_row = QHBoxLayout()
        self.lbl_ff = ElidedLabel("", mode=Qt.ElideMiddle)
        b1 = QPushButton("Elegir…")
        b1.clicked.connect(self._choose_ffmpeg)
        b2 = QPushButton("Automático")
        b2.setToolTip("Buscar ffmpeg junto al programa, en el PATH o en imageio-ffmpeg")
        b2.clicked.connect(self._auto_ffmpeg)
        ff_row.addWidget(self.lbl_ff, 1)
        ff_row.addWidget(b1)
        ff_row.addWidget(b2)
        field("ffmpeg", ff_row)
        self._ffmpeg_custom = s["ffmpeg_custom"]
        self._refresh_ff()

        yt_row = QHBoxLayout()
        from yt_dlp.version import __version__ as ytv
        yt_row.addWidget(label(f"Versión {ytv}", "muted"), 1)
        self.btn_update = QPushButton(" Buscar actualización")
        self.btn_update.setIcon(icon("retry", C["text2"], 14))
        self.btn_update.clicked.connect(self._update)
        yt_row.addWidget(self.btn_update)
        field("yt-dlp", yt_row)

        v.addLayout(g)
        v.addSpacing(6)
        btns, ok, _ = _buttons(self, "Guardar", self._save, "Cancelar")
        v.addLayout(btns)

    def _upd_example(self):
        self.lbl_example.setText("Ejemplo: " + template_example(self.edt_tpl.text()))

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.edt_dir.text())
        if d:
            self.edt_dir.setText(d)

    def _refresh_ff(self):
        path = M.find_ffmpeg(self._ffmpeg_custom)
        if path:
            self.lbl_ff.setText(("✓ " if not self._ffmpeg_custom else "✓ Manual: ") + path)
            self.lbl_ff.setStyleSheet(f"color: {C['ok']};")
        else:
            self.lbl_ff.setText("No encontrado: sin él no se pueden unir vídeo y audio")
            self.lbl_ff.setStyleSheet(f"color: {C['warn']};")
        self.lbl_ff.setToolTip(path or "")

    def _choose_ffmpeg(self):
        f, _ = QFileDialog.getOpenFileName(self, "Selecciona ffmpeg", "",
                                           "ffmpeg (ffmpeg*.exe ffmpeg);;Todos (*.*)")
        if f:
            self._ffmpeg_custom = f
            self._refresh_ff()

    def _auto_ffmpeg(self):
        self._ffmpeg_custom = ""
        self._refresh_ff()

    def _update(self):
        self.btn_update.setEnabled(False)
        self.btn_update.setText("Actualizando…")
        self.win.update_ytdlp(self._update_done)

    def _update_done(self, ok, _):
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Actualizado ✓" if ok else "Reintentar")

    def _save(self):
        p = self.win.prefs
        p["outdir"] = self.edt_dir.text().strip() or p["outdir"]
        p["template"] = self.edt_tpl.text().strip() or DEFAULT_TEMPLATE
        p["parallel"] = self.cmb_parallel.currentData()
        p["retries"] = self.cmb_retries.currentData()
        p["cookies"] = self.cmb_cookies.currentData()
        p["ffhls"] = self.chk_hls.isChecked()
        p["clipboard"] = self.chk_clip.isChecked()
        p["notify"] = self.chk_notify.isChecked()
        p["ffmpeg_custom"] = self._ffmpeg_custom
        self.win.apply_prefs()
        self.accept()


# ───────────────────────── registro de una descarga ─────────────────────────
class LogDialog(QDialog):
    def __init__(self, parent, title, lines):
        super().__init__(parent)
        self.setWindowTitle("Registro")
        self.resize(720, 440)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        t = ElidedLabel(title, "title")
        v.addWidget(t)
        self.txt = QPlainTextEdit("\n".join(lines) or "Sin mensajes.")
        self.txt.setObjectName("log")
        self.txt.setReadOnly(True)
        self.txt.setStyleSheet(f"border: 1px solid {C['line']}; border-radius: 8px;")
        v.addWidget(self.txt, 1)
        row, _, _ = _buttons(self)
        cp = QPushButton(" Copiar")
        cp.setIcon(icon("copy", C["text2"], 14))
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.txt.toPlainText()))
        row.insertWidget(0, cp)
        v.addLayout(row)


# ───────────────────────── editor de volumen ─────────────────────────
class AudioEditorDialog(QDialog):
    """Sube o baja el volumen de un audio ya descargado."""

    def __init__(self, win, path=None):
        super().__init__(win)
        self.win = win
        self.worker = None
        self.setWindowTitle("Editor de volumen")
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        v.addWidget(label("Editor de volumen", "h1"))
        v.addWidget(label("Se vuelve a codificar en el mismo formato (en mp3, m4a u opus la "
                          "pérdida es mínima). Al subir, los picos se limitan para que no "
                          "distorsione.", "muted", wrap=True))

        row = QHBoxLayout()
        self.cmb_file = QComboBox()
        self.cmb_file.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_file.setMinimumContentsLength(24)
        self.cmb_file.setPlaceholderText("Elige un audio descargado o pulsa Examinar…")
        self.cmb_file.currentIndexChanged.connect(lambda *_: self.lbl_stats.setText(""))
        b = QPushButton("Examinar…")
        b.clicked.connect(self._browse)
        row.addWidget(self.cmb_file, 1)
        row.addWidget(b)
        v.addLayout(row)
        for f in win.recent_audio():
            self.add_file(f, select=False)
        if path:
            self.add_file(path)

        row = QHBoxLayout()
        self.btn_analyze = QPushButton(" Medir volumen")
        self.btn_analyze.setIcon(icon("info", C["text2"], 14))
        self.btn_analyze.clicked.connect(self._analyze)
        self.lbl_stats = label("", "muted", wrap=True)
        row.addWidget(self.btn_analyze)
        row.addWidget(self.lbl_stats, 1)
        v.addLayout(row)

        v.addWidget(hsep())
        v.addWidget(label("CAMBIO DE VOLUMEN", "section"))
        self.gain = GainControl()
        self.gain.changed.connect(self._upd_factor)
        v.addWidget(self.gain)
        presets = QHBoxLayout()
        presets.setSpacing(6)
        for db in (-6, -3, 0, 3, 6):
            pb = QPushButton("0 dB" if db == 0 else f"{db:+d} dB".replace("-", "−"))
            pb.setObjectName("flat")
            pb.clicked.connect(lambda _=False, d=db: self.gain.setValue(d))
            presets.addWidget(pb)
        presets.addStretch(1)
        self.lbl_factor = label("", "faint")
        presets.addWidget(self.lbl_factor)
        v.addLayout(presets)

        self.chk_copy = QCheckBox("Conservar el original (guardar como copia)")
        self.chk_copy.setChecked(True)
        v.addWidget(self.chk_copy)
        self.lbl_status = label("", "muted", wrap=True)
        v.addWidget(self.lbl_status)
        btns, self.btn_apply, _ = _buttons(self, "Aplicar volumen", self._apply)
        v.addLayout(btns)
        self._upd_factor(0.0)

    def current(self):
        d = self.cmb_file.currentData()
        return Path(d) if d else None

    def add_file(self, path, select=True):
        path = str(path)
        i = self.cmb_file.findData(path)
        if i < 0:
            self.cmb_file.insertItem(0, Path(path).name, path)
            self.cmb_file.setItemData(0, path, Qt.ToolTipRole)
            i = 0
        if select or self.cmb_file.currentIndex() < 0:
            self.cmb_file.setCurrentIndex(i)

    def _browse(self):
        exts = " ".join(f"*{e}" for e in sorted(M.AUDIO_EXTS))
        f, _ = QFileDialog.getOpenFileName(self, "Elegir audio", self.win.prefs["outdir"],
                                           f"Audio ({exts});;Todos los archivos (*.*)")
        if f:
            self.add_file(f)

    def _upd_factor(self, db):
        self.lbl_factor.setText(f"amplitud ×{10 ** (db / 20):.2f}")

    def _busy(self, on):
        self.btn_analyze.setEnabled(not on)
        self.btn_apply.setEnabled(not on)

    def _check(self):
        if not self.win.ffmpeg:
            self._status("Hace falta ffmpeg (configúralo en Ajustes).", "warn")
            return None
        f = self.current()
        if not f or not f.exists():
            self._status("Elige primero un archivo de audio existente.", "warn")
            return None
        return f

    def _status(self, text, kind="muted"):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet("" if kind == "muted" else f"color: {C[kind]};")

    def _run(self, fn, ok, *args):
        self._busy(True)
        self.worker = FuncWorker(fn, *args)
        self.worker.ok.connect(ok)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _failed(self, msg):
        self._busy(False)
        self._status("✖ " + msg[:300], "err")

    def _analyze(self):
        f = self._check()
        if f:
            self._status("")
            self.lbl_stats.setText("Midiendo…")
            self._run(M.audio_stats, self._analyzed, self.win.ffmpeg, f)

    def _analyzed(self, res):
        self._busy(False)
        mean, peak = res
        if peak == float("-inf"):
            self.lbl_stats.setText("El audio está en silencio.")
        else:
            self.lbl_stats.setText(f"Medio {mean:.1f} dB  ·  pico {peak:.1f} dB  ·  "
                                   f"margen {-peak:.1f} dB")

    def _apply(self):
        f = self._check()
        if not f:
            return
        db = self.gain.value()
        if db == 0:
            self._status("Elige un cambio distinto de 0 dB.", "warn")
            return
        if self.chk_copy.isChecked():
            dst = M.unique_path(f.with_name(f"{f.stem} ({db:+g} dB){f.suffix}"))
        else:
            dst = None
            if QMessageBox.question(self, "Sobrescribir",
                                    f"Se sustituirá el archivo original:\n{f.name}\n\n"
                                    "¿Continuar?") != QMessageBox.Yes:
                return
        self._status("Procesando…")
        self._run(M.change_volume, self._applied, self.win.ffmpeg, f, db, dst)

    def _applied(self, out):
        self._busy(False)
        self.lbl_stats.setText("")
        self._status(f"✓ Guardado: {Path(out).name}", "ok")
        self.add_file(out)


# ───────────────────────── editor de corte ─────────────────────────
class CutterDialog(QDialog):
    """Recorta un vídeo o audio ya descargado eligiendo el fragmento mientras se reproduce."""

    def __init__(self, win, path=None):
        super().__init__(win)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.QMediaPlayer = QMediaPlayer
        self.win = win
        self.path = None
        self.worker = None
        self._sel_play = False
        self.setWindowTitle("Recortar")
        self.resize(960, 720)
        self.setMinimumSize(720, 560)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        top = QHBoxLayout()
        self.lbl_file = ElidedLabel("Ningún archivo", "title", Qt.ElideMiddle)
        b = QPushButton(" Abrir otro…")
        b.setIcon(icon("open", C["text2"], 14))
        b.clicked.connect(self._browse)
        top.addWidget(self.lbl_file, 1)
        top.addWidget(b)
        v.addLayout(top)

        # vídeo (o aviso de audio / vacío)
        self.video_stack = QStackedWidget()
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #000;")
        self.video.setMinimumHeight(240)
        self.placeholder = label("", "muted")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet(f"background: {C['field']}; border-radius: 8px;"
                                       f"color: {C['text3']}; font-size: 14px;")
        self.video_stack.addWidget(self.video)
        self.video_stack.addWidget(self.placeholder)
        v.addWidget(self.video_stack, 1)

        # transporte
        tr = QHBoxLayout()
        tr.setSpacing(4)
        self.btn_play = tool_button("play", "Reproducir / pausa (Espacio)", self._toggle, 18,
                                    C["text"])
        tr.addWidget(tool_button("back", "Atrás 5 s (Mayús+←)", lambda: self._step(-5000)))
        tr.addWidget(self.btn_play)
        tr.addWidget(tool_button("forward", "Adelante 5 s (Mayús+→)", lambda: self._step(5000)))
        tr.addSpacing(8)
        self.lbl_time = label("0:00.0 / 0:00.0", "mono")
        tr.addWidget(self.lbl_time)
        tr.addStretch(1)
        tr.addWidget(tool_button("volume", "Volumen de reproducción", None, 14))
        from PySide6.QtWidgets import QSlider
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(100)
        self.vol.setAccessibleName("Volumen de reproducción")
        tr.addWidget(self.vol)
        v.addLayout(tr)

        self.timeline = Timeline()
        v.addWidget(self.timeline)

        # marcas
        mk = QHBoxLayout()
        mk.setSpacing(8)
        b_in = QPushButton("Marcar inicio")
        b_in.setToolTip("Usa la posición actual como inicio (tecla I)")
        b_in.clicked.connect(self._mark_in)
        self.edt_in = QLineEdit()
        self.edt_in.setObjectName("time")
        self.edt_in.setFixedWidth(90)
        self.edt_in.setAccessibleName("Inicio del corte")
        b_out = QPushButton("Marcar fin")
        b_out.setToolTip("Usa la posición actual como final (tecla O)")
        b_out.clicked.connect(self._mark_out)
        self.edt_out = QLineEdit()
        self.edt_out.setObjectName("time")
        self.edt_out.setFixedWidth(90)
        self.edt_out.setAccessibleName("Final del corte")
        for e in (self.edt_in, self.edt_out):
            e.editingFinished.connect(self._edited)
        self.lbl_len = label("", "muted")
        b_sel = QPushButton(" Reproducir selección")
        b_sel.setIcon(icon("play", C["text2"], 12))
        b_sel.clicked.connect(self._play_selection)
        mk.addWidget(b_in)
        mk.addWidget(self.edt_in)
        mk.addSpacing(6)
        mk.addWidget(b_out)
        mk.addWidget(self.edt_out)
        mk.addSpacing(10)
        mk.addWidget(self.lbl_len)
        mk.addStretch(1)
        mk.addWidget(b_sel)
        v.addLayout(mk)

        v.addWidget(hsep())

        # salida
        out = QGridLayout()
        out.setHorizontalSpacing(16)
        out.setVerticalSpacing(8)
        out.setColumnStretch(1, 1)
        out.addWidget(label("Modo", "muted"), 0, 0)
        self.mode = Segmented([("fast", "Rápido", None), ("precise", "Preciso", None)])
        self.mode.changed.connect(self._upd_mode_hint)
        self.lbl_mode = label("", "faint", wrap=True)
        mrow = QHBoxLayout()
        mrow.addWidget(self.mode)
        mrow.addWidget(self.lbl_mode, 1)
        out.addLayout(mrow, 0, 1)
        out.addWidget(label("Guardar", "muted"), 1, 0)
        drow = QHBoxLayout()
        self.rb_copy = QRadioButton("Como copia nueva")
        self.rb_over = QRadioButton("Sobrescribir el original")
        self.rb_copy.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_copy)
        grp.addButton(self.rb_over)
        drow.addWidget(self.rb_copy)
        drow.addSpacing(12)
        drow.addWidget(self.rb_over)
        drow.addStretch(1)
        out.addLayout(drow, 1, 1)
        v.addLayout(out)
        self._upd_mode_hint("fast")

        # progreso y botones
        self.bar = ProgressLine(4)
        self.bar.hide()
        v.addWidget(self.bar)
        bottom = QHBoxLayout()
        self.lbl_status = label("", "muted")
        self.lbl_status.setWordWrap(True)
        self.btn_show = QPushButton("Mostrar en la carpeta")
        self.btn_show.setObjectName("flat")
        self.btn_show.hide()
        bottom.addWidget(self.lbl_status, 1)
        bottom.addWidget(self.btn_show)
        self.btn_close = QPushButton("Cerrar")
        self.btn_close.clicked.connect(self._close_or_cancel)
        self.btn_cut = QPushButton(" Recortar")
        self.btn_cut.setObjectName("primary")
        self.btn_cut.setIcon(icon("cut", C["on_accent"], 14))
        self.btn_cut.clicked.connect(self._cut)
        bottom.addWidget(self.btn_close)
        bottom.addWidget(self.btn_cut)
        v.addLayout(bottom)

        # reproductor
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.8)
        self.vol.valueChanged.connect(lambda x: self.audio_out.setVolume(x / 100))
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.hasVideoChanged.connect(self._on_has_video)
        self.player.errorOccurred.connect(
            lambda _e, msg: self._status(f"No se puede reproducir: {msg}", "err"))
        self.timeline.seek.connect(self.player.setPosition)
        self.timeline.rangeChanged.connect(self._on_range)

        for keys, fn in (("Space", self._toggle), ("I", self._mark_in), ("O", self._mark_out),
                         ("Left", lambda: self._step(-1000)),
                         ("Right", lambda: self._step(1000)),
                         ("Shift+Left", lambda: self._step(-5000)),
                         ("Shift+Right", lambda: self._step(5000)),
                         ("Home", lambda: self.player.setPosition(self.timeline.a)),
                         ("End", lambda: self.player.setPosition(self.timeline.b))):
            QShortcut(QKeySequence(keys), self, fn)

        self._set_enabled(False)
        self.btn_play.setFocus()
        if path:
            self.load(path)
        else:
            self.placeholder.setText("Abre un vídeo o un audio para recortarlo")
            self.video_stack.setCurrentIndex(1)

    # ── archivo ──
    def _browse(self):
        exts = " ".join(f"*{e}" for e in sorted(M.VIDEO_EXTS | M.AUDIO_EXTS))
        start = str(Path(self.path).parent) if self.path else self.win.prefs["outdir"]
        f, _ = QFileDialog.getOpenFileName(self, "Abrir vídeo o audio", start,
                                           f"Vídeo y audio ({exts});;Todos (*.*)")
        if f:
            self.load(f)

    def load(self, path, keep_range=False):
        self.path = str(path)
        self.lbl_file.setText(Path(path).name)
        self.lbl_file.setToolTip(self.path)
        if not keep_range:
            self.timeline.a = self.timeline.b = 0
        self.player.setSource(QUrl.fromLocalFile(self.path))
        self.video_stack.setCurrentIndex(0)
        self._status("")
        self.btn_show.hide()
        self._set_enabled(True)
        self.player.pause()   # muestra el primer fotograma

    def _release(self):
        self.player.stop()
        self.player.setSource(QUrl())   # Windows bloquea el archivo mientras está abierto

    # ── reproductor ──
    def _toggle(self):
        if not self.path:
            return
        if self.player.playbackState() == self.QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self._sel_play = False
            self.player.play()

    def _step(self, ms):
        if self.path:
            self.player.setPosition(max(0, min(self.timeline.dur, self.player.position() + ms)))

    def _play_selection(self):
        if not self.path:
            return
        self.player.setPosition(self.timeline.a)
        self._sel_play = True
        self.player.play()

    def _on_duration(self, ms):
        self.timeline.setDuration(ms)
        if self.timeline.b <= 0:
            self.timeline.setRange(0, ms)
        self._sync_fields()
        self._on_position(self.player.position())

    def _on_position(self, ms):
        self.timeline.setPosition(ms)
        self.lbl_time.setText(f"{M.fmt_clock(ms / 1000)} / {M.fmt_clock(self.timeline.dur / 1000)}")
        if self._sel_play and ms >= self.timeline.b:
            self._sel_play = False
            self.player.pause()

    def _on_state(self, st):
        playing = st == self.QMediaPlayer.PlayingState
        self.btn_play.setIcon(icon("pause" if playing else "play", C["text"], 18))

    def _on_has_video(self, has):
        if not has and self.path:
            self.placeholder.setText("♪  " + Path(self.path).name)
        self.video_stack.setCurrentIndex(0 if has else 1)

    # ── marcas ──
    def _mark_in(self):
        if self.path:
            pos = self.player.position()
            self.timeline.setRange(pos, max(self.timeline.b, pos + 100))
            self._sync_fields()

    def _mark_out(self):
        if self.path:
            pos = self.player.position()
            self.timeline.setRange(min(self.timeline.a, max(0, pos - 100)), pos)
            self._sync_fields()

    def _on_range(self, a, b):
        self._sync_fields()

    def _edited(self):
        try:
            a = M.parse_time(self.edt_in.text())
            b = M.parse_time(self.edt_out.text())
        except ValueError:
            self._status("Tiempo no válido. Usa 90, 1:30 o 1:02:03.5", "warn")
            self._sync_fields()
            return
        a = 0 if a is None else a * 1000
        b = self.timeline.dur if b is None else b * 1000
        if b <= a:
            self._status("El final debe ser posterior al inicio.", "warn")
            self._sync_fields()
            return
        self._status("")
        self.timeline.setRange(a, b)
        self._sync_fields()

    def _sync_fields(self):
        a, b = self.timeline.a, self.timeline.b
        self.edt_in.setText(M.fmt_clock(a / 1000))
        self.edt_out.setText(M.fmt_clock(b / 1000))
        self.lbl_len.setText(f"Duración del corte: {M.fmt_clock((b - a) / 1000)}")

    def _upd_mode_hint(self, mode):
        self.lbl_mode.setText(
            "Sin pérdida de calidad e instantáneo. El inicio se ajusta al fotograma clave "
            "más cercano (puede empezar un poco antes)." if mode == "fast" else
            "Corta en el punto exacto. Vuelve a codificar, así que tarda más y pierde un "
            "poco de calidad.")

    # ── recortar ──
    def _set_enabled(self, on):
        self.btn_cut.setEnabled(on)
        self.timeline.setEnabled(on)

    def _status(self, text, kind="muted"):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet("" if kind == "muted" else f"color: {C[kind]};")

    def _cut(self):
        if not self.path:
            return
        if not self.win.ffmpeg:
            self._status("Hace falta ffmpeg (configúralo en Ajustes).", "warn")
            return
        a, b = self.timeline.a / 1000, self.timeline.b / 1000
        if b - a < 0.1:
            self._status("Selecciona un fragmento más largo.", "warn")
            return
        if a <= 0.05 and b >= self.timeline.dur / 1000 - 0.05:
            self._status("La selección es el archivo entero: mueve los marcadores.", "warn")
            return
        src = Path(self.path)
        if self.rb_over.isChecked():
            if QMessageBox.question(self, "Sobrescribir",
                                    f"Se sustituirá el archivo original:\n{src.name}\n\n"
                                    "No se puede deshacer. ¿Continuar?") != QMessageBox.Yes:
                return
            dst = None
        else:
            tag = f"{M.fmt_clock(a)}-{M.fmt_clock(b)}".replace(":", "'")
            dst = M.unique_path(src.with_name(f"{src.stem} (corte {tag}){src.suffix}"))
        self._range_ms = (self.timeline.a, self.timeline.b)
        self._release()
        precise = self.mode.value() == "precise"
        self.worker = CutWorker(self.win.ffmpeg, src, a, b, precise, dst)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.ok.connect(self._cut_ok)
        self.worker.failed.connect(self._cut_failed)
        self.worker.cancelled.connect(self._cut_cancelled)
        self.bar.show()
        self.bar.setValue(0)
        if not precise:
            self.bar.setBusy(True)
        self._set_enabled(False)
        self.btn_close.setText("Cancelar")
        self._status("Recortando…")
        self.worker.start()

    def _finish(self):
        self.btn_close.setText("Cerrar")
        self._set_enabled(True)
        self.bar.hide()

    def _cut_ok(self, out):
        self._finish()
        self._status(f"✓ Guardado: {Path(out).name}", "ok")
        self.btn_show.show()
        try:
            self.btn_show.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.btn_show.clicked.connect(lambda: reveal(out))
        self.win.file_created(out)
        # se vuelve a cargar el original (o el sustituto) conservando la selección
        self.load(self.path if Path(self.path).exists() else out, keep_range=True)
        self.timeline.setRange(*self._range_ms)
        self._sync_fields()

    def _cut_failed(self, msg):
        self._finish()
        self._status("✖ " + msg[:400], "err")
        self.load(self.path, keep_range=True)

    def _cut_cancelled(self):
        self._finish()
        self._status("Recorte cancelado.")
        self.load(self.path, keep_range=True)

    def _close_or_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
        else:
            self.reject()

    def done(self, r):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self._release()
        super().done(r)
