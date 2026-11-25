"""Local navigation over the imported originals. Page numbers are one-based."""
import re


def select_page_numbers(spec, page_count):
    if not spec.strip():
        return None
    selected = set()
    for part in re.split(r"[,，、]", spec.strip()):
        bounds = re.fullmatch(r"\s*([0-9]+)(?:\s*[-–—至]\s*([0-9]+))?\s*", part)
        if not bounds:
            raise ValueError("请填写文件页码或连续页段，例如 8-16，23")
        first = int(bounds[1])
        last = int(bounds[2] or first)
        if not 1 <= first <= last <= page_count:
            raise ValueError(f"页码应在 1 到 {page_count} 之间，且起页在止页之前")
        selected.update(range(first, last + 1))
    return sorted(selected)


def catalogue(store, source_id, terms=()):
    if store.one("SELECT 1 FROM kb_book WHERE source_id=?",(source_id,)):
        from .knowledge_index import find_pages
        hits=find_pages(store,source_id," ".join(terms),limit=12 if terms else None)
        if not terms:
            sections=[]
            for page in hits:
                if sections and page["chapter"]==sections[-1]["chapter"]:
                    sections[-1]["last_page"]=page["page"]
                else:sections.append({**page,"last_page":page["page"]})
            hits=sections
        return {"source_id":source_id,"total_pages":store.one("SELECT count(*) n FROM pages WHERE source_id=?",(source_id,))["n"],"pages":hits}
    pages = store.all("SELECT number,text,image FROM pages WHERE source_id=? ORDER BY number", (source_id,))
    if not pages:
        raise ValueError("原件尚未入库或没有阅读页")
    result = []
    terms = [term.strip().casefold() for term in terms if term.strip()]
    for page in pages:
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        useful = [line for line in lines if not re.fullmatch(r"(?:第\s*)?\d+\s*(?:页)?", line)]
        # This is a directory. The read operation returns the entire selected page.
        heading = " / ".join(useful[:2])[:110]
        matches = [line for line in lines if any(term in line.casefold() for term in terms)]
        if not terms or matches:
            result.append({"page":page["number"], "heading":heading or "图像页",
                           **({"matching_lines":matches} if terms else {}),
                           "image_only":not bool(useful) and bool(page["image"])})
    return {"source_id":source_id,"total_pages":len(pages),"pages":result}


def read_ranges(agent, args, experiment_id, allowed, job_id):
    pages = {}
    for interval in args["ranges"]:
        first, last = interval["first"], interval["last"]
        if not 1 <= first <= last:
            raise ValueError("页段应为从1开始的闭区间")
        total = agent.store.one("SELECT COUNT(*) AS n FROM pages WHERE source_id=?", (args["source_id"],))["n"]
        if last > total:
            raise ValueError(f"原件共有{total}页")
        pages.update(dict.fromkeys(range(first,last+1)))
    return {"source_id":args["source_id"],"pages":[agent.dispatch("read_page", {"source_id":args["source_id"],"page":n},
            experiment_id,allowed,job_id) for n in pages]}


def illustration_bounds(region, geometry):
    """Recover the edges of nearly enclosed native figures in page coordinates."""
    import pymupdf
    if not region or not geometry:
        return region
    selected=pymupdf.Rect(region)
    native=[]
    for item in geometry.get('regions',[]):
        rect=pymupdf.Rect(item['region'])
        if item['kind']=='嵌入图片' and rect.get_area()>0 and (selected & rect).get_area() >= .8*rect.get_area():
            native.append(rect)
    if not native:
        return region
    figure=pymupdf.Rect(native[0])
    for rect in native[1:]:
        figure |= rect
    # A page excerpt may contain small illustrations and important surrounding
    # text. Match an actual figure-sized request before removing a source caption.
    combined=selected | figure
    if (selected & figure).get_area() < .55*combined.get_area():
        return region
    for cap in geometry.get('captions',[]):
        label=pymupdf.Rect(cap['region'])
        if (label.y1>=figure.y1 and figure.y1-.015 <= label.y0 <= figure.y1+.05
                and label.x0<figure.x1 and label.x1>figure.x0 and combined.y1>=label.y0):
            combined.y1=max(figure.y1,min(combined.y1,label.y0))
    return list(combined)


def figure_regions(store, source_id, number):
    import pymupdf
    src = store.one("SELECT kind,original FROM sources WHERE id=?", (source_id,))
    if not src:
        raise ValueError("原件未入库")
    original = store.asset_path(src["original"])
    if src["kind"] == "docx":
        original = original.parent/"reading.pdf"
    if src["kind"] not in ("pdf","docx") or not original.is_file():
        return {"regions":[],"notice":"无PDF对象坐标，可用view_page和source_anchor自由圈选。"}
    with pymupdf.open(original) as doc:
        if not 1 <= number <= len(doc):
            raise ValueError("页码不存在")
        page = doc[number-1]
        bounds = page.rect
        result = []
        def add(rect, kind):
            rect = (pymupdf.Rect(rect)*page.rotation_matrix) & bounds
            if rect.is_empty or min(rect.width,rect.height)<8 or rect.get_area()>=bounds.get_area()*.95:
                return
            box = [round(v,6) for v in (rect.x0/bounds.width,rect.y0/bounds.height,rect.x1/bounds.width,rect.y1/bounds.height)]
            if any(r["region"]==box for r in result):
                return
            result.append({"kind":kind,"region":box})
        for info in page.get_image_info():
            add(info["bbox"],"嵌入图片")
        for rect in page.cluster_drawings():
            add(rect,"矢量笔画组")
        captions = []
        for block in page.get_text("blocks",sort=True):
            words=block[4].strip() if block[6]==0 else ""
            if re.match(r"(?:图\s*[\d一二三四五六七八九十]|Fig(?:ure)?\.?\s*\d)",words,re.I):
                rect=pymupdf.Rect(block[:4])*page.rotation_matrix
                captions.append({"text":words,"region":[rect.x0/bounds.width,rect.y0/bounds.height,rect.x1/bounds.width,rect.y1/bounds.height]})
        return {"source_id":source_id,"page":number,"regions":result,"captions":captions,
                "notice":"候选来自原件对象几何，可能漏掉外部标注或包含装饰。选能读懂关系的框，必要时合并图、端子及标注；source_anchor返回真实裁图。"}
