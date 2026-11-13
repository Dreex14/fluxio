"""Sistema visual: tokens de color, tipografía, hoja de estilos e iconos.

Los iconos se dibujan con la fuente de iconos de Windows (Segoe Fluent Icons en
Windows 11, Segoe MDL2 Assets en Windows 10): no hace falta incluir imágenes.
"""
import atexit
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap

# ── colores ── (granate del icono como identidad, ámbar para la acción principal)
C = dict(
    bg="#1b0e13",          # fondo de la ventana
    surface="#231319",     # cabecera y barra de estado
    raised="#2c1820",      # paneles desplegables, tarjetas
    field="#150a0e",       # campos de texto
    hover="#36202a",
    line="#3a222b",
    line2="#56303f",
    text="#f6ecef",
    text2="#c7aab4",
    text3="#8c707b",
    brand="#a8324f",
    accent="#f2a93b",
    accent_hover="#ffbd5c",
    on_accent="#2a0f17",
    ok="#62d394",
    err="#ff6f7a",
    warn="#f2c14e",
)
MODE_COLORS = {"video": "#a8324f", "audio": "#2c6e60", "gif": "#9a6416"}

FONT_UI = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", monospace'

# ── iconos ──
GLYPHS = dict(
    download=0xE896, play=0xE768, pause=0xE769, stop=0xE71A, folder=0xE8B7, open=0xE8E5,
    close=0xE711, retry=0xE72C, settings=0xE713, cut=0xE8C6, volume=0xE767, more=0xE712,
    paste=0xE77F, link=0xE71B, video=0xE714, music=0xEC4F, image=0xEB9F, log=0xE8A5,
    check=0xE73E, error=0xE783, warning=0xE7BA, tools=0xE90F, chevdown=0xE70D,
    chevup=0xE70E, copy=0xE8C8, info=0xE946, prev=0xE892, next=0xE893, globe=0xE774,
    back=0xE72B, forward=0xE72A,
)
FALLBACK = dict(
    download="↓", play="▶", pause="❚❚", stop="■", folder="▤", open="↗", close="✕",
    retry="↻", settings="⚙", cut="✂", volume="♪", more="⋯", paste="⎘", link="🔗",
    video="▶", music="♪", image="▣", log="≡", check="✓", error="!", warning="!",
    tools="⚒", chevdown="▾", chevup="▴", copy="⎘", info="i", prev="⏮", next="⏭",
    globe="◍", back="←", forward="→",
)
_font_family = None


def _glyph_family():
    global _font_family
    if _font_family is None:
        fams = set(QFontDatabase.families())
        _font_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                             if f in fams), "")
    return _font_family


def glyph_pixmap(name, color, size=16):
    app = QGuiApplication.instance()
    dpr = max(2.0, app.devicePixelRatio() if app else 1.0)
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    fam = _glyph_family()
    if fam:
        f = QFont(fam)
        f.setPixelSize(int(size))
        ch = chr(GLYPHS[name])
    else:
        f = QFont("Segoe UI Symbol")
        f.setPixelSize(int(size * 0.85))
        ch = FALLBACK.get(name, "?")
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, ch)
    p.end()
    return pm


def icon(name, color=None, size=16):
    ic = QIcon()
    ic.addPixmap(glyph_pixmap(name, color or C["text2"], size), QIcon.Normal)
    ic.addPixmap(glyph_pixmap(name, C["text3"], size), QIcon.Disabled)
    return ic


# ── recursos para la hoja de estilos (flecha de los desplegables, marca de casilla) ──
_assets = None


def _asset_dir():
    global _assets
    if _assets is None:
        _assets = Path(tempfile.mkdtemp(prefix="fluxio_ui_"))
        atexit.register(shutil.rmtree, _assets, True)
        glyph_pixmap("chevdown", C["text2"], 10).save(str(_assets / "chev.png"))
        glyph_pixmap("check", C["on_accent"], 12).save(str(_assets / "check.png"))
    return _assets


def stylesheet():
    a = _asset_dir().as_posix()
    return f"""
* {{ font-family: {FONT_UI}; font-size: 13px; color: {C['text']}; }}
QMainWindow, QDialog, QWidget#root {{ background: {C['bg']}; }}
QWidget#transparent {{ background: transparent; }}
QToolTip {{ background: {C['raised']}; color: {C['text']}; border: 1px solid {C['line2']};
    padding: 5px 8px; border-radius: 4px; }}

QFrame#header {{ background: {C['surface']}; border-bottom: 1px solid {C['line']}; }}
QFrame#statusbar {{ background: {C['surface']}; border-top: 1px solid {C['line']}; }}
QFrame#panel {{ background: {C['raised']}; border: 1px solid {C['line']}; border-radius: 10px; }}
QFrame#clip {{ background: {C['hover']}; border: 1px solid {C['line2']}; border-radius: 8px; }}
QFrame#segbox {{ background: {C['field']}; border: 1px solid {C['line2']}; border-radius: 8px; }}
QFrame#jobrow {{ background: transparent; border: none; border-bottom: 1px solid {C['line']}; }}
QFrame#jobrow:hover {{ background: {C['surface']}; }}
QFrame#sep {{ background: {C['line']}; border: none; }}
QFrame#video {{ background: #000; border: 1px solid {C['line']}; border-radius: 8px; }}

QLabel#title {{ font-size: 15px; font-weight: 600; }}
QLabel#h1 {{ font-size: 18px; font-weight: 600; }}
QLabel#section {{ color: {C['text3']}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#muted {{ color: {C['text2']}; }}
QLabel#faint {{ color: {C['text3']}; font-size: 12px; }}
QLabel#jobtitle {{ font-weight: 600; }}
QLabel#mono {{ font-family: {FONT_MONO}; font-size: 12px; color: {C['text2']}; }}

QLineEdit, QComboBox, QPlainTextEdit {{
    background: {C['field']}; border: 1px solid {C['line2']}; border-radius: 6px;
    padding: 5px 9px; selection-background-color: {C['accent']};
    selection-color: {C['on_accent']}; }}
QLineEdit, QComboBox {{ min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {C['accent']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {C['text3']}; border-color: {C['line']}; }}
QLineEdit#urlbar {{ font-size: 14px; padding: 8px 10px; border-radius: 8px; }}
QLineEdit#time {{ font-family: {FONT_MONO}; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({a}/chev.png); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {C['raised']}; border: 1px solid {C['line2']};
    outline: none; padding: 4px; selection-background-color: {C['hover']};
    selection-color: {C['text']}; }}
QPlainTextEdit#log {{ font-family: {FONT_MONO}; font-size: 12px; color: {C['text2']};
    border: none; border-radius: 0; background: {C['field']}; padding: 8px 12px; }}

QPushButton {{ background: {C['raised']}; border: 1px solid {C['line2']}; border-radius: 6px;
    padding: 6px 14px; font-weight: 600; }}
QPushButton:hover {{ background: {C['hover']}; border-color: {C['text3']}; }}
QPushButton:pressed {{ background: {C['field']}; }}
QPushButton:focus {{ border-color: {C['accent']}; }}
QPushButton:disabled {{ color: {C['text3']}; border-color: {C['line']}; }}
QPushButton#primary {{ background: {C['accent']}; color: {C['on_accent']};
    border: 1px solid {C['accent']}; padding: 7px 18px; }}
QPushButton#primary:hover {{ background: {C['accent_hover']}; border-color: {C['accent_hover']}; }}
QPushButton#primary:focus {{ border: 1px solid {C['text']}; }}
QPushButton#primary:disabled {{ background: {C['line2']}; border-color: {C['line2']};
    color: {C['text3']}; }}
QPushButton#flat {{ background: transparent; border: 1px solid transparent; color: {C['text2']};
    padding: 4px 10px; font-weight: 600; }}
QPushButton#flat:hover {{ background: {C['hover']}; color: {C['text']}; }}
QPushButton#flat:checked {{ color: {C['accent']}; }}
QPushButton#flat:focus {{ border-color: {C['line2']}; }}
QPushButton#seg {{ background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 5px 14px; color: {C['text2']}; }}
QPushButton#seg:hover {{ color: {C['text']}; background: {C['hover']}; }}
QPushButton#seg:checked {{ background: {C['accent']}; color: {C['on_accent']}; }}
QPushButton#seg:focus {{ border-color: {C['text3']}; }}
QPushButton#chip {{ background: transparent; border: 1px solid {C['line2']}; border-radius: 15px;
    padding: 5px 12px; color: {C['text']}; font-weight: 400; text-align: left; }}
QPushButton#chip:hover {{ border-color: {C['text3']}; background: {C['hover']}; }}
QPushButton#chip:checked {{ border-color: {C['accent']}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 5px; }}
QToolButton:hover {{ background: {C['hover']}; }}
QToolButton:pressed {{ background: {C['field']}; }}
QToolButton:focus {{ border-color: {C['line2']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {C['text3']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {C['text3']}; background: {C['field']}; }}
QCheckBox::indicator:hover {{ border-color: {C['text2']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']};
    image: url({a}/check.png); }}
QCheckBox::indicator:focus {{ border-color: {C['accent_hover']}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px;
    border: 1px solid {C['text3']}; background: {C['field']}; }}
QRadioButton::indicator:checked {{ border-color: {C['accent']}; background: qradialgradient(
    cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {C['accent']}, stop:0.45 {C['accent']},
    stop:0.5 {C['field']}, stop:1 {C['field']}); }}

QSlider::groove:horizontal {{ height: 4px; background: {C['line2']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {C['text']}; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {C['accent_hover']}; }}

QMenu {{ background: {C['raised']}; border: 1px solid {C['line2']}; padding: 5px;
    border-radius: 8px; }}
QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {C['hover']}; }}
QMenu::item:disabled {{ color: {C['text3']}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {C['line']}; margin: 4px 6px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {C['line2']}; border-radius: 3px; min-height: 30px;
    margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {C['text3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {C['line2']}; border-radius: 3px; min-width: 30px;
    margin: 2px 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {C['line']}; }}
QDialogButtonBox QPushButton {{ min-width: 80px; }}
"""
