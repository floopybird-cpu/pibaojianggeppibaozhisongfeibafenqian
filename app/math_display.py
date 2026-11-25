"""Render one LaTeX expression consistently for the native reader and exported worksheets."""
from functools import lru_cache
from xml.etree import ElementTree as ET

from latex2mathml.converter import convert

from .math_fonts import math_object


@lru_cache(maxsize=512)
def raster(tex, size=16, scale=2):
    from PySide6 import QtCore, QtGui
    from PySide6.QtSvg import QSvgRenderer
    try:
        svg = QSvgRenderer(math_object(convert(tex), size).svg().encode("utf-8"))
    except Exception as exc:
        raise ValueError("公式无法排版") from exc
    logical = svg.defaultSize()
    if not logical.isValid() or logical.isEmpty():
        raise ValueError("公式尺寸无效")
    image = QtGui.QImage(logical * scale, QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    try:
        svg.render(painter)
    finally:
        painter.end()
    return logical, image


def png(image):
    from PySide6 import QtCore
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("公式图片写入失败")
    return bytes(buffer.data())


@lru_cache(maxsize=512)
def linear_text(tex):
    """Readable math for clipboard, screen readers and plain-text-only controls."""
    sup = dict(zip("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"))
    sub = dict(zip("0123456789+-aeioxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋ₐₑᵢₒₓₕₖₗₘₙₚₛₜ"))
    def walk(node):
        tag = node.tag.split("}")[-1]
        children = [walk(child) for child in node]
        if tag in ("msub", "msup") and len(children) == 2:
            base, script = children
            table = sub if tag == "msub" else sup
            if script and all(c in table for c in script):
                return base + "".join(table[c] for c in script)
            return base + ("(" if tag=="msub" else "^(") + script + ")"
        if tag == "mfrac" and len(children) == 2:
            return "(" + children[0] + ")/(" + children[1] + ")"
        if tag == "msqrt":
            return "√(" + "".join(children) + ")"
        return (node.text or "") + "".join(children)
    try:
        return walk(ET.fromstring(convert(tex)))
    except Exception as exc:
        raise ValueError("公式无法转换为文字") from exc
