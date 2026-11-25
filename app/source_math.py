"""Recover printed scripts from the cited PDF glyphs while retaining verbatim citation text."""
from functools import lru_cache
from pathlib import Path
import re

from .scientific_markup import textbook

START = re.compile(r"(?<![A-Za-z])(?:[A-Za-zΑ-ω]|[Δ△][A-Za-z])\s*=")
RUN = re.compile(r"[A-Za-zΑ-ω0-9=.\-−+*/^_(){}∫∑△∆·×±≤≥≈，, \t]+")
PAGE = re.compile(r"physlab://page/([A-Za-z0-9_-]+)/([1-9]\d*)")


def key(text):
    return re.sub(r"\s+", "", text).replace("∆","Δ").replace("△","Δ")


@lru_cache(maxsize=512)
def printed_formulas(path, sha, number):
    import pymupdf
    found = {}
    with pymupdf.open(path) as pdf:
        if number < 1 or number > len(pdf):
            return {}
        for block in pdf[number-1].get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
            for line in block.get("lines", ()):
                spans = line["spans"]
                text = "".join(s["text"] for s in spans)
                for start in START.finditer(text):
                    match = RUN.match(text,start.start())
                    if not match:
                        continue
                    raw = match[0].strip().rstrip(",，")
                    base = next((s for s in spans if "=" in s["text"]), None)
                    if not base:
                        continue
                    offset, pieces, scripted = 0, [], False
                    for s in spans:
                        span_start = offset
                        offset += len(s["text"])
                        left, right = max(start.start(),span_start), min(start.start()+len(raw),offset)
                        if left >= right:
                            continue
                        bit = text[left:right]
                        if (0.45*base["size"] <= s["size"] < 0.86*base["size"] and pieces
                            and re.fullmatch(r"[A-Za-z0-9,+−-]+",bit)):
                            delta_y = s["origin"][1]-base["origin"][1]
                            if s["flags"] & 1 or delta_y < -0.18*base["size"]:
                                bit = "^{"+bit+"}"
                            elif delta_y >= -0.05*base["size"]:
                                bit = "_{"+bit+"}"
                            scripted |= bit.startswith(("^{", "_{"))
                        pieces.append(bit)
                    if scripted:
                        tex = textbook("".join(pieces).replace("，",r",\quad "))
                        found.setdefault(key(raw),set()).add(tex)
    return {flat:next(iter(forms)) for flat, forms in found.items() if len(forms)==1}


def formulas_for_source(store, source, page):
    row = store.one("SELECT original,kind,sha FROM sources WHERE id=?", (source,))
    if not row or row["kind"] not in ("pdf", "docx"):
        return {}
    try:
        path = store.asset_path(row["original"])
        if row["kind"] == "docx":
            path = store.asset_path(str(Path(row["original"]).with_name("reading.pdf")))
        return printed_formulas(str(path),row["sha"],page)
    except (OSError, ValueError, RuntimeError):
        return {}


def resolve_quote(store, quote, href):
    location = PAGE.fullmatch(href)
    if location and store is not None:
        return formulas_for_source(store, location[1], int(location[2])).get(key(quote))
