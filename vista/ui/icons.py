"""Vector icon set (24x24 line icons drawn for RANE VISTA), rendered through QtSvg and cached.
`icon(name, color)` returns a crisp QIcon at any size; 'currentColor' in a path is replaced by the colour."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

P = {
    # ribbon
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1.2"/><rect x="14" y="3" width="7" height="5" rx="1.2"/>'
                 '<rect x="14" y="12" width="7" height="9" rx="1.2"/><rect x="3" y="16" width="7" height="5" rx="1.2"/>',
    "job": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2.8h6V4"/><path d="M9 10h6M9 14h6M9 18h3.5"/>',
    "steps": '<path d="M10 6h11M10 12h11M10 18h11"/><path d="M4 5l1.2-1V9"/><path d="M3.6 13.2c.4-.8 2.4-.9 2.4.4 0 1-2.4 1.6'
             '-2.4 3h2.6"/>',
    "detection": '<path d="M3 7.5V5a2 2 0 0 1 2-2h2.5M16.5 3H19a2 2 0 0 1 2 2v2.5M21 16.5V19a2 2 0 0 1-2 2h-2.5M7.5 21H5a2 2'
                 ' 0 0 1-2-2v-2.5"/><path d="M6.5 12s2-4 5.5-4 5.5 4 5.5 4-2 4-5.5 4-5.5-4-5.5-4z"/>'
                 '<circle cx="12" cy="12" r="1.6"/>',
    "process": '<rect x="3" y="3" width="7" height="6" rx="1.2"/><rect x="14" y="15" width="7" height="6" rx="1.2"/>'
               '<path d="M10 6h4.5a2.5 2.5 0 0 1 2.5 2.5V15"/><path d="M14.6 12.6 17 15l2.4-2.4"/>'
               '<path d="M6.5 9v6.5A2.5 2.5 0 0 0 9 18h5"/>',
    "connectivity": '<path d="M9 2.5v5M15 2.5v5"/><path d="M6 7.5h12V11a6 6 0 0 1-12 0z"/><path d="M12 17v4.5"/>',
    "operate": '<circle cx="12" cy="12" r="9"/><path d="M10 8.4v7.2l6-3.6z" fill="currentColor"/>',
    "calibration": '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.6"/><path d="M12 1.8v4.2M12 18v4.2M1.8 12h4.2'
                   'M18 12h4.2"/>',
    "hardware": '<rect x="6" y="6" width="12" height="12" rx="1.6"/><rect x="9.5" y="9.5" width="5" height="5" rx=".6"/>'
                '<path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8'
             'M18 14.3a6.4 6.4 0 0 1 3.5 6.2"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.8"/>'
            '<circle cx="12" cy="17" r=".7" fill="currentColor"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.6" r=".7" fill="currentColor"/>',
    # general
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M9 7V4h6v3"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>', "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "arrow_left": '<path d="M19 12H5M11 18l-6-6 6-6"/>', "arrow_right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow_up": '<path d="M12 19V5M6 11l6-6 6 6"/>', "arrow_down": '<path d="M12 5v14M18 13l-6 6-6-6"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "camera": '<path d="M3 8.5A2 2 0 0 1 5 6.5h2L8.5 4h7L17 6.5h2a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
              '<circle cx="12" cy="13" r="3.6"/>',
    "projector": '<rect x="2" y="8" width="20" height="10" rx="2"/><circle cx="16" cy="13" r="3"/><path d="M5.5 11.5h5M5.5 14.5'
                 'h3M9 8 7 5M15 8l2-3"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="1.6"/><path d="M8 20h8M12 16v4"/>',
    "barcode": '<path d="M3 7V4h3M18 4h3v3M21 17v3h-3M6 20H3v-3"/><path d="M7 8v8M10 8v8M12.5 8v8M15 8v8M17 8v8"/>',
    "database": '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13M4 12c0 1.7 3.6 3 8'
                ' 3s8-1.3 8-3"/>',
    "check_circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.7 2.7L16.5 9"/>',
    "x_circle": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.3" r=".7" fill="currentColor"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>', "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "reset": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/>',
    "zoom_in": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4M11 8v6M8 11h6"/>',
    "zoom_out": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4M8 11h6"/>',
    "fit": '<path d="M3 9V3h6M21 9V3h-6M3 15v6h6M21 15v6h-6"/>',
    "expand": '<path d="m7 15 5 5 5-5M7 9l5-5 5 5"/>', "collapse": '<path d="m7 20 5-5 5 5M7 4l5 5 5-5"/>',
    "indent": '<path d="M3 5h18M11 12h10M11 19h10M3 9.5l4 2.5-4 2.5"/>',
    "outdent": '<path d="M3 5h18M11 12h10M11 19h10M7 9.5 3 12l4 2.5"/>',
    "cube": '<path d="m12 2.5 8.5 4.8v9.4L12 21.5l-8.5-4.8V7.3z"/><path d="m3.5 7.3 8.5 4.8 8.5-4.8M12 12.1v9.4"/>',
    "hand": '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11M11 10V4a1.5 1.5 0 0 1 3 0v7M14 10.5V6a1.5 1.5 0 0 1 3 0v8a7 7 0 0 1-7'
            ' 7h-.5a6 6 0 0 1-4.8-2.4L3.4 16a1.5 1.5 0 0 1 2.4-1.8L8 16"/>',
    "button": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4" fill="currentColor"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.8"/><path d="m21 16-5-5-9 9"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
    "release": '<path d="M12 2.5l2.4 2.1 3.2-.3.8 3.1 2.8 1.6-1.2 3 1.2 3-2.8 1.6-.8 3.1-3.2-.3L12 21.5l-2.4-2.1-3.2.3-.8'
               '-3.1-2.8-1.6 1.2-3-1.2-3 2.8-1.6.8-3.1 3.2.3z"/><path d="m8.6 12.3 2.4 2.4 4.4-4.6"/>',
    "variants": '<circle cx="7" cy="7" r="4"/><rect x="13" y="13" width="8" height="8" rx="1.2"/><path d="m17 3 4 7h-8z"/>'
                '<path d="M3 17h6M6 14v6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "variable": '<path d="M8 3c-2 0-3 1-3 3v3c0 1-1 3-2 3 1 0 2 2 2 3v3c0 2 1 3 3 3M16 3c2 0 3 1 3 3v3c0 1 1 3 2 3-1 0-2 2'
                '-2 3v3c0 2-1 3-3 3"/><path d="m9.5 9 5 6M14.5 9l-5 6"/>',
    "dialog": '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9.5h8M8 12.5h5"/>',
    "instruction": '<path d="M6 2.5h8l4 4V21a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 21z"/><path d="M14 2.5v4h4M9 12h6M9 16h6"/>',
    "composite": '<path d="m12 2.5 9.5 4.8-9.5 4.8-9.5-4.8z"/><path d="m2.5 12 9.5 4.8 9.5-4.8M2.5 16.8l9.5 4.8 9.5-4.8"/>',
    "delay": '<path d="M6 2.5h12M6 21.5h12M7.5 2.5c0 5 9 5 9 9.5s-9 4.5-9 9.5M16.5 2.5c0 5-9 5-9 9.5s9 4.5 9 9.5"/>',
    "place": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 20h16"/>', "take": '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M4 20h16"/>',
    "grid": '<rect x="3" y="3" width="18" height="18" rx="1.5"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3'
           'l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    "lock": '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
    "key": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.8-9.8M17 6l3 3M14.5 8.5l2.5 2.5"/>',
    "export": '<path d="M14 3h5v5M19 3l-8 8"/><path d="M19 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"/>',
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H7.5L5 19H3z"/><path d="M5 19 7.5 10H22l-2.5 9z"/>',
    "save": '<path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3M7 21v-7h10v7"/>',
    "teach": '<path d="M15.5 4.5 19.5 8.5 9 19l-5 1 1-5z"/><path d="m13.5 6.5 4 4"/>',
    "wizard": '<path d="m4 20 11-11M14 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 11l.6 1.4L21 13l-1.4.6L19 15l-.6-1.4L17 13l1.4-.6z"/>',
    "log": '<path d="M5 3h14v18H5z"/><path d="M8.5 7.5h7M8.5 11.5h7M8.5 15.5h4"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    "min": '<path d="M5 12h14"/>', "max": '<rect x="5.5" y="5.5" width="13" height="13" rx="1"/>',
    "restore": '<rect x="5" y="8.5" width="10.5" height="10.5" rx="1"/><path d="M8.5 8.5V5H19v10.5h-3.5"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "filter": '<path d="M3 5h18l-7 8.5V20l-4-2v-4.5z"/>',
    "dot": '<circle cx="12" cy="12" r="5" fill="currentColor"/>',
    # new icons for detections and steps
    "tool": '<path d="m14.7 6.3 3 3-9 9H5.7v-3l9-9z"/><path d="m13.5 7.5 3 3M17 4a3 3 0 0 1 3 3l-1.5 1.5-4.5-4.5L15.5 2.5A3 3 0 0 1 17 4z"/>',
    "bin": '<path d="M4 8h16l-2 12H6L4 8z"/><path d="M3 5h18v3H3z"/><path d="M10 12v5M14 12v5"/>',
    "text": '<path d="M4 7V4h16v3M12 4v16M9 20h6"/>',
    "group": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="8.5" y="14" width="7" height="7" rx="1.5"/><path d="M6.5 10v2a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2v-2M12 14v-2"/>',
    "cad": '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
    "draft": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
    "sound": '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    "function": '<path d="M9 18V6c0-2.5 1.5-3 3-3M6 12h8"/>',
    "counter": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 9v6M12 12h4M14 10v4"/>',
}


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#142B54", size: int = 24, stroke: float = 1.7) -> QPixmap:
    body = P.get(name, P["info"]).replace("currentColor", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size * scale, size * scale))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str = "#142B54", size: int = 24, disabled: str | None = "#A9B2BE") -> QIcon:
    ic = QIcon(pixmap(name, color, size))
    if disabled:
        ic.addPixmap(pixmap(name, disabled, size), QIcon.Disabled)
    return ic
