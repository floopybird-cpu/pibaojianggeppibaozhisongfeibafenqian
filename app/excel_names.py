"""Excel worksheet labels; preserve source section text outside this adapter.

Office adds naming restrictions beyond the OOXML string schema. Full-width
reserved punctuation is also excluded by Excel; openpyxl only checks ASCII.
See https://faq.nec-lavie.jp/fa/qa/web/knowledge23526.html and MS-OI29500 2.1.578.
"""
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile


FORBIDDEN = frozenset("[]:*?/\\\x00\x03")
RESERVED = frozenset(("history", "历史记录", "履歴"))


def forbidden(character):
    return character in FORBIDDEN or unicodedata.normalize("NFKC", character) in FORBIDDEN


def _clip(text, units):
    # Excel's UTF-16 length limit includes both units of a supplementary symbol.
    return text.encode("utf-16-le")[:units * 2].decode("utf-16-le", errors="ignore")


def sheet_name(value, used):
    # Fold for the forbidden-character comparison only; do not rewrite Greek,
    # superscript or other meaningful characters throughout the section title.
    value = "".join("_" if forbidden(c) else c for c in (value or "未分组记录"))
    value = re.sub(r"^[\s'＇]+|[\s'＇]+$", "", value) or "未分组记录"
    base = _clip(value, 31).rstrip("'＇") or "未分组记录"
    candidate, number = base, 1
    while candidate.casefold() in used or candidate.casefold() in RESERVED:
        number += 1
        suffix = f"（{number}）"
        candidate = _clip(base, 31 - len(suffix)) + suffix
    used.add(candidate.casefold())
    return candidate


def validate_exported_names(path):
    """Inspect the serialized labels, not the producer library's acceptance."""
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    names = [s.attrib["name"] for s in workbook.findall(
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets/"
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")]
    seen = set()
    for name in names:
        if (not name.strip() or len(name.encode("utf-16-le")) > 62
                or any(forbidden(c) for c in name) or name[0] in "'＇"
                or name[-1] in "'＇" or name.casefold() in seen
                or name.casefold() in RESERVED):
            raise ValueError("Excel工作表标签不兼容：" + repr(name))
        seen.add(name.casefold())
    if not names:
        raise ValueError("Excel未生成工作表")
    return names
