"""CJK glyphs for vector math, using only this Windows user's installed fonts.

Temporary composite fonts are never placed in document assets or a release.
Word keeps native OMML; PDF receives explicit glyph paths, including mtext.
"""
from pathlib import Path
from functools import lru_cache
import hashlib
import tempfile
import threading
import os
from xml.etree import ElementTree as ET

_lock=threading.RLock()


@lru_cache(maxsize=64)
def _font(characters):
    import ziamath
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.transformPen import TransformPen
    base=Path(ziamath.__file__).parent/'fonts/STIXTwoMath-Regular.ttf'
    if not base.is_file():base=next(Path(ziamath.__file__).parent.rglob('STIXTwoMath-Regular.ttf'))
    sources=[p for p in (Path('C:/Windows/Fonts/msyh.ttc'),Path('C:/Windows/Fonts/simsun.ttc'),Path('C:/Windows/Fonts/simsunb.ttf')) if p.is_file()]
    version='cjk-outline-2'+str(base.stat().st_mtime_ns)+''.join(str(p.stat().st_mtime_ns) for p in sources)
    identity=hashlib.sha256((version+characters).encode()).hexdigest()
    directory=Path(tempfile.gettempdir())/'PhysicsHarnessMathFonts'/str(os.getpid());directory.mkdir(parents=True,exist_ok=True)
    target=directory/(identity+'.ttf')
    with _lock:
        if target.exists():return str(target)
        font=TTFont(base);order=list(font.getGlyphOrder());cmap=font.getBestCmap();remaining={ord(c) for c in characters if ord(c) not in cmap}
        for source in sources:
            if not remaining:break
            fallback=TTFont(source,fontNumber=0);source_map=fallback.getBestCmap();glyphs=fallback.getGlyphSet();scale=font['head'].unitsPerEm/fallback['head'].unitsPerEm
            for cp in sorted(remaining.intersection(source_map)):
                original=source_map[cp];name='physicsCJK'+str(cp)
                pen=TTGlyphPen(None);record=DecomposingRecordingPen(glyphs);glyphs[original].draw(record);record.replay(TransformPen(pen,(scale,0,0,scale,0,0)))
                font['glyf'][name]=pen.glyph();order.append(name)
                advance,bearing=fallback['hmtx'][original];font['hmtx'][name]=(round(advance*scale),round(bearing*scale))
                for table in font['cmap'].tables:
                    if table.isUnicode() and (table.format in (12,13) or cp<=65535 and table.format==4):table.cmap[cp]=name
                remaining.remove(cp)
            fallback.close()
        if remaining:
            font.close();raise ValueError('当前系统字体缺少公式中文字形：'+''.join(chr(c) for c in sorted(remaining)))
        # fontTools compresses equal trailing hmtx advances. ziafont 0.11
        # reads only the long metrics and otherwise falls back to max advance.
        # A non-mapped zero-width final glyph keeps all real CJK metrics long.
        sentinel='physicsMetricsSentinel'
        font['glyf'][sentinel]=TTGlyphPen(None).glyph();font['hmtx'][sentinel]=(0,0);order.append(sentinel)
        font.setGlyphOrder(order);font['maxp'].numGlyphs=len(order)
        # One process/lock owns this directory; no cross-process reader can
        # observe a half-written font, and no rename permission is required.
        font.save(target);font.close()
    return str(target)


def math_object(mathml,size):
    import ziamath
    # PDF and Qt's SVG readers require explicit SVG 1.1 glyph paths.
    # Keep this invariant in the shared entry point, including direct users.
    ziamath.config.svg2=False
    root=ET.fromstring(mathml) if isinstance(mathml,str) else mathml
    characters=''.join(sorted({c for c in ''.join(root.itertext()) if '\u2e80'<=c<='\u9fff' or '\uf900'<=c<='\ufaff' or '\U00020000'<=c<='\U0003134f'}))
    return ziamath.Math(root,size=size,font=_font(characters) if characters else None)
