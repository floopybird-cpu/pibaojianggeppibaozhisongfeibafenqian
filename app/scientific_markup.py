"""Shared math tokens for native chat, worksheets, chart captions and document composition."""
import re

from markdown_it.token import Token
from functools import lru_cache

COMMAND = r"\\(?:frac|dfrac|tfrac|sqrt|sum|int|bar|overline|mathrm|alpha|beta|gamma|Delta|delta|theta|lambda|mu|rho|sigma|eta|pi|omega|tau|phi|cdot|times)\b"
SCRIPT = r"(?:[_^](?:\{[A-Za-z0-9,+\-]+\}|[A-Za-z0-9]+))"
# Only conventional symbols qualify as a bare left-hand side. Program identifiers
# and code samples keep their spelling. Explicit math delimiters take precedence.
SYMBOL = r"(?:[Δδ][A-Za-z]|[A-Za-zΑ-ω])(?:" + SCRIPT + r")?"
BOUND = r"(?<![A-Za-z0-9_./\\@])"
MATH_CHAR = r"A-Za-zΑ-ω0-9_.+\-−±*/^·×∙∆△∑Σ∫√=≤≥≠≈,{}() \t"
EQUATION = re.compile(BOUND + SYMBOL + r"\s*[=≈≤≥]\s*[" + MATH_CHAR + r"]+")
INDEXED = re.compile(BOUND + r"(?:[Δδ][A-Za-z]|[A-Za-zΑ-ω])" + SCRIPT + r"(?![A-Za-z0-9_.\\@])")
PRODUCT = re.compile(BOUND + SYMBOL + r"\s*(?:[·×∙/]\s*" + SYMBOL + r")+(?![A-Za-z0-9_.])")
RAW = re.compile(COMMAND)
LITERAL = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|[A-Za-z]:[\\/])\S+|[\w.-]+@[\w.-]+|[A-Za-z0-9_.-]+\.[A-Za-z]{2,6}\b")
GREEK = {"Δ":"Delta","δ":"delta","∆":"Delta","△":"Delta","η":"eta","θ":"theta","λ":"lambda","μ":"mu",
         "ρ":"rho","σ":"sigma","τ":"tau","ω":"omega","φ":"phi","π":"pi","α":"alpha","β":"beta",
         "∑":"sum","∫":"int","×":"times","·":"cdot","∙":"cdot","±":"pm","≤":"le","≥":"ge","≠":"ne","≈":"approx"}
EXPLICIT = re.compile(r"(?<!\\)(\$\$(.+?)(?<!\\)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|\$(?!\s)([^$\n]+?)(?<![\\\s])\$)", re.S)
FORMULA_STYLE = r"物理公式和带上下标的量均用 LaTeX，行内用 \(…\)，独立公式用 \[…\]。反引号用于代码、文件名。原页证据可保留原文，回答用可辨认的数学符号重述。"


def command_end(text, pos):
    match = RAW.match(text, pos)
    if not match:
        return pos
    end = match.end()
    for _ in range(2):
        brace = re.match(r"\s*\{", text[end:])
        if not brace:
            break
        depth = 1
        i = end + brace.end()
        while i < len(text) and depth:
            depth += (text[i] == "{") - (text[i] == "}")
            i += 1
        if depth:
            return pos
        end = i
    return end


def textbook(value):
    value = value.replace("−", "-")
    value = re.sub(r"([_^])\{?([A-Za-z0-9]+(?:,[A-Za-z0-9]+)*)\}?", lambda m:
                   m[1]+"{"+(r"\mathrm{"+m[2]+"}" if len(m[2])>1 and m[2].isalpha() else m[2])+"}", value)
    return "".join("\\"+GREEK[c]+" " if c in GREEK else c for c in value)


def bare_parts(value):
    protected = [m.span() for m in LITERAL.finditer(value)]
    pos = 0
    while pos < len(value):
        matches = [m for rx in (EQUATION, RAW, PRODUCT, INDEXED) if (m := rx.search(value, pos))]
        if not matches:
            yield False, value[pos:]
            break
        m = min(matches, key=lambda v:v.start())
        start, end = m.span()
        covered = next((b for a,b in protected if a <= start < b), None)
        if covered:
            yield False, value[pos:covered]
            pos = covered
            continue
        if m.re is RAW:
            end = command_end(value, start)
        if m.re is EQUATION:
            # Exclude a prose closing parenthesis and the sentence punctuation.
            raw = value[start:end].rstrip(" .,")
            prose = re.search(r"\s+(?:[A-Za-z]{3,})\b(?!\s*[_^])", raw)
            if prose:
                raw = raw[:prose.start()].rstrip()
            while raw.endswith(")") and raw.count(")") > raw.count("("):
                raw = raw[:-1].rstrip()
            end = start + len(raw)
        if end <= start:
            yield False, value[pos:start+1]
            pos = start+1
            continue
        if start > pos:
            yield False, value[pos:start]
        yield True, value[start:end] if m.re is RAW else textbook(value[start:end])
        pos = end


def parts(value):
    """Recognise explicit TeX everywhere a human-readable label is accepted."""
    pos = 0
    for m in EXPLICIT.finditer(str(value)):
        yield from bare_parts(value[pos:m.start()])
        yield True, next(g for g in m.groups()[1:] if g is not None)
        pos = m.end()
    yield from bare_parts(value[pos:])


@lru_cache(maxsize=512)
def plot_text(text):
    from matplotlib.mathtext import MathTextParser
    from .math_display import linear_text
    output = []
    for math, value in parts(text):
        if math:
            try:
                MathTextParser("path").parse("$" + value + "$")
                output.append("$" + value + "$")
            except ValueError:
                output.append(linear_text(value))
        else:
            output.append(value)
    return "".join(output)


def math_code(text):
    # A fenced program or identifier is never reinterpreted as an equation.
    explicit = EXPLICIT.fullmatch(text)
    if explicit:
        return next(g for g in explicit.groups()[1:] if g is not None)
    if RAW.search(text) and re.fullmatch(r"[A-Za-zΑ-ω0-9\\{}_, .^=+*/|()\[\]-]+", text) and text.count("{") == text.count("}"):
        return text
    if any(c in text for c in "∑∫△∆") and "=" in text and re.fullmatch("["+MATH_CHAR+"，]+",text):
        return textbook(text.replace("，", r",\quad "))
    return None


def install(md):
    def inline_display(state, silent):
        text, pos = state.src, state.pos
        if not text.startswith(("$$", r"\["), pos):
            return False
        close = "$$" if text.startswith("$$", pos) else r"\]"
        end = text.find(close, pos + 2, state.posMax)
        if end < 0:
            return False
        if not silent:
            token = state.push("math_inline", "math", 0)
            token.content = text[pos+2:end].strip()
        state.pos = end + 2
        return True
    md.inline.ruler.before("escape", "embedded_display_math", inline_display)

    def math_environment(state, start, end, silent):
        line = state.src[state.bMarks[start]+state.tShift[start]:state.eMarks[start]]
        match = re.match(r"\\begin\{(align\*?|equation\*?|gather\*?)\}", line)
        if not match:
            return False
        close = r"\end{" + match[1] + "}"
        finish = start
        while finish < end:
            if state.src[state.bMarks[finish]:state.eMarks[finish]].strip().endswith(close):
                break
            finish += 1
        if finish == end:
            return False
        if not silent:
            raw = state.getLines(start,finish+1,state.blkIndent,False).strip()
            body = raw[len(match[0]):-len(close)].strip()
            environment = match[1].rstrip("*")
            if environment in ("align","gather"):
                wrapper = "aligned" if environment == "align" else "gathered"
                body = r"\begin{" + wrapper + "}" + body + r"\end{" + wrapper + "}"
            token = state.push("math_block","math",0)
            token.block, token.map, token.content = True, [start,finish+1], body
        state.line = finish+1
        return True
    md.block.ruler.before("paragraph", "display_environment", math_environment)

    def normalize(state):
        for block in state.tokens:
            if block.type != "inline" or not block.children:
                continue
            children = []
            for index, token in enumerate(block.children):
                if token.type == "code_inline":
                    math = math_code(token.content)
                    resolve = state.env.get("source_formula")
                    if math and resolve:
                        following = next((t for t in block.children[index+1:] if t.type != "text" or t.content.strip()), None)
                        if following and following.type == "link_open":
                            math = resolve(token.content, following.attrGet("href") or "") or math
                    if math:
                        token.type, token.tag, token.content = "math_inline", "math", math
                if token.type != "text":
                    children.append(token)
                    continue
                for math, content in bare_parts(token.content):
                    part = Token("math_inline" if math else "text", "math" if math else "", 0)
                    part.content = content
                    children.append(part)
            block.children = children
    md.core.ruler.after("text_join", "scientific_math", normalize)
