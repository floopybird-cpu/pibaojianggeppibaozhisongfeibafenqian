"""Choose an entry surface from the measurement design, also for saved workbenches."""
from collections import defaultdict
import re


def samples(table, page):
    mode = table.get("entry_mode")
    if mode in ("groups", "samples"):
        return mode == "samples"
    text = " ".join(str(table.get(k, "")) for k in ("title", "id"))
    axes = {p.get("x") for p in page.get("plots", []) if p.get("table") == table.get("id")}
    if table.get("selection"):
        axes.add(table["selection"].get("column"))
    for c in table["columns"]:
        if c.get("unit") not in ("s", "ms", "μs", "µs", "us", "ns", "min", "秒", "毫秒"):
            continue
        name = c["id"] + " " + c["label"]
        clock = re.search(r"采样时[间刻]|时间戳|时间轴|timestamp|sample_time|time_s\b|(?<!\w)time(?!\w)|^t\s|时刻",name,re.I)
        stream = re.search(r"示波器|同步记录|波形|连续采[样集]|时间序列|waveform|scope|time.series",text,re.I)
        if clock and (c["id"] in axes or stream) or stream and re.search(r"时间|time",name,re.I):
            return True
    return False


def pairing(table):
    """Explicit paired slots, or legacy side/order columns with documented directions."""
    columns = {c["id"]:c for c in table["columns"]}
    design = table.get("grouping")
    if design:
        return design
    sides = [c for c in columns.values() if re.search(r"侧别|左右侧|测量侧|^side$", c["label"])]
    conditions = [c for c in columns.values() if re.search(r"级次|序号|档位|设定值", c["label"])]
    if len(sides) == len(conditions) == 1:
        text = str(sides[0].get("hint","")) + str(sides[0].get("reading_hint",""))
        if "左" in text and "右" in text:
            return {"condition":conditions[0]["id"],"variant":sides[0]["id"],"variants":["左","右"]}
    return None


def entry_groups(rows, pair):
    if not pair:
        return [[i] for i in range(len(rows))]
    condition, variant, tags = pair["condition"], pair["variant"], pair["variants"]
    groups = []
    i = 0
    while i < len(rows):
        chunk = rows[i:i+len(tags)]
        labels = [str(row.get(variant,"")).strip() for row in chunk]
        conditions = {str(row.get(condition,"")).strip() for row in chunk}
        empty = all(not any(str(v).strip() for k,v in row.items() if not k.startswith("_")) for row in chunk)
        paired = (len(chunk)==len(tags) and len(conditions)==1 and
                  (empty or sorted(labels)==sorted(tags)))
        count = len(tags) if paired else 1
        groups.append(list(range(i,i+count)))
        i += count
    # Instruments and notebooks can put all forward runs before all reverse runs.
    # Bind separated readings only when the recorded condition identifies a unique set.
    unmatched = defaultdict(list)
    for block in groups:
        if len(block) != 1:
            continue
        index = block[0]
        value = str(rows[index].get(condition, "")).strip()
        if value and str(rows[index].get(variant, "")).strip() in tags:
            unmatched[value].append(index)
    joined = {}
    covered = set()
    for indices in unmatched.values():
        labels = [str(rows[i][variant]).strip() for i in indices]
        if sorted(labels) == sorted(tags):
            joined[indices[0]] = indices
            covered.update(indices[1:])
    return [joined.get(group[0], group) for group in groups if group[0] not in covered]
