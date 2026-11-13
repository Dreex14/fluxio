"""Componentes reutilizables de la interfaz."""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider,
    QToolButton, QVBoxLayout, QWidget,
)

from . import media as M
from .theme import C, MODE_COLORS, glyph_pixmap, icon


def label(text="", name=None, wrap=False):
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    if wrap:
        lb.setWordWrap(True)
    return lb


def tool_button(glyph, tip, slot=None, size=16, color=None):
    b = QToolButton()
    b.setIcon(icon(glyph, color, size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def hsep():
    f = QFrame()
    f.setObjectName("sep")
    f.setFixedHeight(1)
    return f


def vsep(h=16):
    f = QFrame()
    f.setObjectName("sep")
    f.setFixedSize(1, h)
    return f


class ElidedLabel(QLabel):
    """Etiqueta de una línea que recorta el texto con «…» según el ancho disponible."""

    def __init__(self, text="", name=None, mode=Qt.ElideRight):
        super().__init__()
        if name:
            self.setObjectName(name)
        self._full, self._mode = "", mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._refresh()

    def text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def _refresh(self):
        w = max(self.width(), 20)
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, self._mode, w))


class ProgressLine(QWidget):
    """Barra de progreso fina; en modo ocupado muestra un segmento que se desliza."""

    def __init__(self, height=3):
        super().__init__()
        self.setFixedHeight(height)
        self._v, self._busy, self._phase = 0.0, False, 0.0
        self._color = QColor(C["accent"])
        self._timer = QTimer(self, interval=30, timeout=self._tick)

    def setColor(self, color):
        self._color = QColor(color)
        self.update()

    def setValue(self, pct):
        self._busy = False
        self._timer.stop()
        self._v = max(0.0, min(100.0, pct))
        self.update()

    def setBusy(self, on=True):
        if on and not self._busy:
            self._busy = True
            self._timer.start()
        elif not on:
            self._busy = False
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.012) % 1.3
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["line"]))
        p.drawRoundedRect(r, rad, rad)
        p.setBrush(self._color)
        if self._busy:
            w = r.width() * 0.3
            x = (self._phase - 0.3) * r.width()
            seg = QRectF(x, 0, w, r.height()).intersected(r)
            if seg.width() > 0:
                p.drawRoundedRect(seg, rad, rad)
        elif self._v > 0:
            p.drawRoundedRect(QRectF(0, 0, r.width() * self._v / 100, r.height()), rad, rad)


class Thumb(QWidget):
    """Miniatura con esquinas redondeadas (recorta la imagen para llenar el hueco)."""

    def __init__(self, w=160, h=90, radius=8):
        super().__init__()
        self.setFixedSize(w, h)
        self._pm, self._radius, self._glyph = None, radius, "video"

    def setPixmap(self, pm):
        self._pm = pm if pm and not pm.isNull() else None
        self.update()

    def setGlyph(self, glyph):
        self._glyph = glyph
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(C["field"]))
        if self._pm:
            s = self._pm.size().scaled(self.size(), Qt.KeepAspectRatioByExpanding)
            x, y = (self.width() - s.width()) / 2, (self.height() - s.height()) / 2
            p.drawPixmap(QRectF(x, y, s.width(), s.height()), self._pm,
                         QRectF(self._pm.rect()))
        else:
            g = glyph_pixmap(self._glyph, C["text3"], 28)
            p.drawPixmap(int((self.width() - 28) / 2), int((self.height() - 28) / 2), g)


class Badge(QWidget):
    """Cuadro de color con el icono del tipo de descarga (vídeo, audio, GIF)."""
    GLYPH = {"video": "video", "audio": "music", "gif": "image"}

    def __init__(self, mode, size=34):
        super().__init__()
        self.setFixedSize(size, size)
        self.mode = mode

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(MODE_COLORS[self.mode]))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        g = glyph_pixmap(self.GLYPH[self.mode], C["text"], 16)
        p.drawPixmap(int((self.width() - 16) / 2), int((self.height() - 16) / 2), g)


class Segmented(QFrame):
    """Selector segmentado (Vídeo | Audio | GIF)."""
    changed = Signal(str)

    def __init__(self, items):
        super().__init__()
        self.setObjectName("segbox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, text, glyph in items:
            b = QPushButton((" " + text) if glyph else text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            if glyph:
                b.setIcon(icon(glyph, C["text2"], 14))
                b.toggled.connect(lambda on, b=b, g=glyph: b.setIcon(
                    icon(g, C["on_accent"] if on else C["text2"], 14)))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.toggled.connect(lambda on, k=key: on and self.changed.emit(k))
        next(iter(self.buttons.values())).setChecked(True)

    def value(self):
        return next(k for k, b in self.buttons.items() if b.isChecked())

    def setValue(self, key):
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class GainControl(QWidget):
    """Deslizador de ganancia en dB (pasos de 0,5 dB) con su valor al lado."""
    changed = Signal(float)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(M.GAIN_MIN * 2), int(M.GAIN_MAX * 2))
        self.slider.setPageStep(2)
        self.slider.setAccessibleName("Cambio de volumen en decibelios")
        self.lbl = label("", "mono")
        self.lbl.setMinimumWidth(64)
        self.lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.lbl)
        self.slider.valueChanged.connect(self._upd)
        self._upd()

    def value(self):
        return self.slider.value() / 2

    def setValue(self, db):
        self.slider.setValue(round(max(M.GAIN_MIN, min(M.GAIN_MAX, float(db))) * 2))

    def _upd(self, *_):
        db = self.value()
        self.lbl.setText("0 dB" if db == 0 else f"{db:+.1f} dB")
        self.changed.emit(db)


# ───────────────────────── fila de la cola ─────────────────────────
class JobRow(QFrame):
    """Una descarga de la cola: tipo, título, estado, progreso y acciones."""
    action = Signal(int, str)

    STATE_COLOR = {"queued": "text3", "running": "accent", "processing": "accent",
                   "done": "ok", "error": "err", "cancelled": "text3"}

    def __init__(self, job):
        super().__init__()
        self.jid = job.id
        self.setObjectName("jobrow")
        self.setAttribute(Qt.WA_Hover)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setMinimumHeight(64)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(12)
        lay.addWidget(Badge(job.cfg["mode"]), 0, Qt.AlignTop)

        mid = QVBoxLayout()
        mid.setSpacing(4)
        self.lbl_title = ElidedLabel("", "jobtitle")
        line2 = QHBoxLayout()
        line2.setSpacing(8)
        self.lbl_meta = label("", "faint")
        self.lbl_status = ElidedLabel("")
        self.lbl_status.setStyleSheet("font-size: 12px;")
        line2.addWidget(self.lbl_meta)
        line2.addWidget(vsep(10))
        line2.addWidget(self.lbl_status, 1)
        self.bar = ProgressLine(3)
        mid.addWidget(self.lbl_title)
        mid.addLayout(line2)
        mid.addWidget(self.bar)
        lay.addLayout(mid, 1)

        self.btns = {}
        box = QWidget()
        box.setObjectName("transparent")
        box.setFixedWidth(5 * 28 + 4 * 2)   # ancho fijo: las barras de progreso quedan alineadas
        acts = QHBoxLayout(box)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(2)
        acts.addStretch(1)
        for key, glyph, tip in (("open", "play", "Abrir archivo"),
                                ("folder", "folder", "Mostrar en la carpeta"),
                                ("cut", "cut", "Recortar…"),
                                ("volume", "volume", "Editar volumen…"),
                                ("retry", "retry", "Reintentar"),
                                ("log", "log", "Ver registro de esta descarga"),
                                ("cancel", "close", "Cancelar"),
                                ("more", "more", "Más acciones")):
            b = tool_button(glyph, tip, lambda _=False, k=key: self._clicked(k))
            self.btns[key] = b
            acts.addWidget(b)
        lay.addWidget(box)
        self.update_job(job)

    def _clicked(self, key):
        if key == "more":
            m = QMenu(self)
            m.addAction(icon("log"), "Ver registro").triggered.connect(
                lambda: self.action.emit(self.jid, "log"))
            m.addAction(icon("copy"), "Copiar enlace").triggered.connect(
                lambda: self.action.emit(self.jid, "copy"))
            m.addAction(icon("globe"), "Abrir enlace en el navegador").triggered.connect(
                lambda: self.action.emit(self.jid, "browser"))
            if self._finished:
                m.addAction(icon("retry"), "Descargar de nuevo").triggered.connect(
                    lambda: self.action.emit(self.jid, "retry"))
                m.addSeparator()
                m.addAction(icon("close"), "Quitar de la lista").triggered.connect(
                    lambda: self.action.emit(self.jid, "remove"))
            m.exec(self.btns["more"].mapToGlobal(self.btns["more"].rect().bottomLeft()))
        else:
            self.action.emit(self.jid, key)

    def update_job(self, job):
        from .engine import describe_cfg
        st = job.state
        self._finished = job.finished
        self.lbl_title.setText(job.title or job.url)
        self.lbl_title.setToolTip(f"{job.title}\n{job.url}" if job.title != job.url else job.url)
        self.lbl_meta.setText(describe_cfg(job.cfg))

        text = job.status
        if st == "running":
            bits = [job.status]
            if job.pct >= 0:
                bits.append(f"{job.pct:.0f} %")
            if job.bps:
                bits.append(M.fmt_speed(job.bps))
            if job.eta:
                bits.append(job.eta)
            text = "  ·  ".join(bits)
        elif st == "done":
            size = sum(Path(f).stat().st_size for f in job.files if Path(f).exists())
            n = len(job.files)
            text = "Completado" + (f"  ·  {M.fmt_size(size)}" if size else "") + \
                   (f"  ·  {n} archivos" if n > 1 else "")
        self.lbl_status.setText(text)
        self.lbl_status.setToolTip(job.error[:800] if st == "error" else "")
        color = C[self.STATE_COLOR[st]]
        self.lbl_status.setStyleSheet(f"font-size: 12px; color: {color};")

        if st == "running" and job.pct >= 0:
            self.bar.setColor(C["accent"])
            self.bar.setValue(job.pct)
        elif job.active:
            self.bar.setColor(C["accent"])
            self.bar.setBusy(True)
        elif st == "done":
            self.bar.setColor(C["ok"])
            self.bar.setValue(100)
        elif st == "error":
            self.bar.setColor(C["err"])
            self.bar.setValue(100)
        else:
            self.bar.setValue(0)
        self.bar.setVisible(st != "cancelled")

        files = [f for f in job.files if Path(f).exists()]
        last = Path(files[-1]) if files else None
        mode = job.cfg["mode"]
        ext = last.suffix.lower() if last else ""
        vis = {
            "open": st == "done" and bool(last),
            "folder": st == "done" and bool(last),
            "cut": st == "done" and bool(last) and mode in ("video", "audio")
                   and ext in (M.VIDEO_EXTS | M.AUDIO_EXTS),
            "volume": st == "done" and bool(last) and mode == "audio" and ext in M.AUDIO_EXTS,
            "retry": st in ("error", "cancelled"),
            "log": st == "error",
            "cancel": not job.finished,
            "more": True,
        }
        for k, b in self.btns.items():
            b.setVisible(vis[k])
        self.btns["cancel"].setToolTip("Quitar de la cola" if st == "queued" else "Cancelar")


class EmptyState(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.addStretch(2)
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("download", C["line2"], 44))
        ic.setAlignment(Qt.AlignCenter)
        v.addWidget(ic)
        v.addSpacing(10)
        t = label("La cola está vacía", "title")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        s = label("Pega un enlace arriba o arrástralo a la ventana.\n"
                  "Ctrl+V pega desde cualquier sitio · Enter descarga", "muted")
        s.setAlignment(Qt.AlignCenter)
        v.addWidget(s)
        v.addStretch(3)


class DropOverlay(QWidget):
    """Capa que aparece al arrastrar enlaces o archivos sobre la ventana."""
    dropped = Signal(object)   # QMimeData

    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(C["bg"])
        bg.setAlpha(225)
        p.fillRect(self.rect(), bg)
        r = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        pen = QPen(QColor(C["accent"]), 2, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 14, 14)
        g = glyph_pixmap("download", C["accent"], 40)
        cx, cy = r.center().x(), r.center().y()
        p.drawPixmap(int(cx - 20), int(cy - 50), g)
        f = QFont(self.font())
        f.setPixelSize(16)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(r.left(), cy + 2, r.width(), 24), Qt.AlignCenter,
                   "Suelta para añadir")
        f.setPixelSize(12)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(QColor(C["text2"]))
        p.drawText(QRectF(r.left(), cy + 28, r.width(), 20), Qt.AlignCenter,
                   "Enlaces → a la barra  ·  vídeos → recortar  ·  audios → editar volumen")

    def dragEnterEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.hide()

    def dropEvent(self, e):
        self.hide()
        self.dropped.emit(e.mimeData())
        e.acceptProposedAction()


# ───────────────────────── línea de tiempo del editor de corte ─────────────────────────
class Timeline(QWidget):
    """Barra de tiempo con marcadores de inicio y fin arrastrables y cabezal de reproducción."""
    seek = Signal(int)              # ms
    rangeChanged = Signal(int, int)  # ms, ms
    PAD = 12

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(46)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.dur = self.pos = self.a = self.b = 0
        self._drag = None

    def setDuration(self, ms):
        self.dur = max(0, int(ms))
        self.a = min(self.a, self.dur)
        self.b = self.dur if self.b <= 0 or self.b > self.dur else self.b
        self.update()

    def setPosition(self, ms):
        self.pos = int(ms)
        self.update()

    def setRange(self, a, b):
        self.a, self.b = max(0, int(a)), min(self.dur, int(b))
        self.update()

    # coordenadas
    def _x(self, ms):
        w = self.width() - 2 * self.PAD
        return self.PAD + (w * ms / self.dur if self.dur else 0)

    def _ms(self, x):
        w = max(1, self.width() - 2 * self.PAD)
        return int(max(0, min(self.dur, (x - self.PAD) / w * self.dur)))

    def _hit(self, x):
        da, db = abs(x - self._x(self.a)), abs(x - self._x(self.b))
        if min(da, db) <= 8:
            return "a" if da <= db else "b"
        return None

    def mousePressEvent(self, e):
        if not self.dur or e.button() != Qt.LeftButton:
            return
        x = e.position().x()
        self._drag = self._hit(x) or "pos"
        self._move(x)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag:
            self._move(x)
        else:
            self.setCursor(Qt.SizeHorCursor if self._hit(x) else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def _move(self, x):
        ms = self._ms(x)
        if self._drag == "a":
            self.a = min(ms, max(0, self.b - 100))
            self.rangeChanged.emit(self.a, self.b)
            self.seek.emit(self.a)
        elif self._drag == "b":
            self.b = max(ms, min(self.dur, self.a + 100))
            self.rangeChanged.emit(self.a, self.b)
            self.seek.emit(self.b)
        else:
            self.pos = ms
            self.seek.emit(ms)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        cy = h / 2 + 4
        left, right = self.PAD, self.width() - self.PAD
        # pista
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["line2"]))
        p.drawRoundedRect(QRectF(left, cy - 4, right - left, 8), 4, 4)
        if not self.dur:
            return
        # selección
        xa, xb = self._x(self.a), self._x(self.b)
        sel = QColor(C["accent"])
        sel.setAlpha(150)
        p.setBrush(sel)
        p.drawRect(QRectF(xa, cy - 4, xb - xa, 8))
        # marcadores
        p.setBrush(QColor(C["accent"]))
        for x in (xa, xb):
            p.drawRoundedRect(QRectF(x - 3, cy - 13, 6, 26), 3, 3)
        p.setPen(QPen(QColor(C["on_accent"]), 1))
        for x in (xa, xb):
            p.drawLine(QPointF(x, cy - 5), QPointF(x, cy + 5))
        # cabezal
        xp = self._x(self.pos)
        p.setPen(QPen(QColor(C["text"]), 2))
        p.drawLine(QPointF(xp, 8), QPointF(xp, h - 2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["text"]))
        p.drawPolygon(QPolygonF([QPointF(xp - 6, 1), QPointF(xp + 6, 1), QPointF(xp, 9)]))
