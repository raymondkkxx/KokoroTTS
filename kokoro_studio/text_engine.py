import html
import json
import re

from kokoro_studio.config import PALETTE_BLACK, PALETTE_ORANGE, PALETTE_ROSE

SPEAKER_PREFIX_RE = re.compile(
    r"^\s*(?:[\*\-\•]\s*)?(?:\*{0,2})(W|Woman|F|Female|M|Man|Male)\s*:(?:\*{0,2})\s*",
    re.IGNORECASE,
)

HTML_TOKEN_RE = re.compile(
    r"(?P<speaker>^(?:\*{0,2})(?:W|Woman|F|Female|M|Man|Male)\s*:(?:\*{0,2}))"
    r"|(?P<weak>\*\([^)]+\)\*)"
    r"|(?P<bold>\*\*[^*]+\*\*)"
    r"|(?P<phonetic>\[[^\]]+\])"
    r"|(?P<silent>\([a-zA-Z\']+\))"
    r"|(?P<tie>‿)"
    r"|(?P<slash>\s*/\s*)",
    re.IGNORECASE,
)

ABBREVIATIONS = [
    r"Mr\.",
    r"Mrs\.",
    r"Ms\.",
    r"Dr\.",
    r"Prof\.",
    r"Sr\.",
    r"Jr\.",
    r"vs\.",
    r"etc\.",
    r"e\.g\.",
    r"i\.e\.",
    r"U\.S\.",
    r"U\.K\.",
    r"approx\.",
    r"dept\.",
    r"est\.",
    r"min\.",
    r"max\.",
    r"no\.",
    r"vol\.",
]


def detect_speaker(line: str):
    """Return ('F'|'M'|'DEFAULT', line_without_speaker_prefix)."""
    m = SPEAKER_PREFIX_RE.match(line)
    if not m:
        return "DEFAULT", line.strip()
    tag = m.group(1).upper()
    speaker = "F" if tag in ("W", "WOMAN", "F", "FEMALE") else "M"
    return speaker, line[m.end() :].strip()


def clean_annotated_for_tts(text: str) -> str:
    """
    Convert annotated learning text into pure, natural English for Kokoro TTS:
    - Strip speaker tags (W:, M:)
    - Remove bracketed phonetics / citations: [ði], [mə-ˈtɪ-kjə-ləs]
    - Restore weak glide syllables: *(to de)* -> to de
    - Restore silent letters: nee(d) -> need
    - Remove stress markers: de**cide** -> decide
    - Convert linking tie '‿' into natural word space
    - Convert sense-group slash '/' into a soft comma pause
    - Strip CJK characters and non-vocal symbols
    """
    if not text:
        return ""

    _, t = detect_speaker(text)

    # Remove all bracketed annotations: [ði], [mə-ˈtɪ-kjə-ləs], etc.
    t = re.sub(r"\[[^\]]*\]", "", t)

    # Unwrap weak-form glide parentheses: *(I)* -> I, *(to de)* -> to de
    t = re.sub(r"\*\(([^)]+)\)\*", r"\1", t)

    # Unwrap silent-letter parentheses: nee(d) -> need, con**sis**(ts) -> con**sis**ts
    t = re.sub(r"\(([a-zA-Z\']+)\)", r"\1", t)

    # Remove bold/italic asterisks without inserting spaces inside words
    t = t.replace("**", "").replace("*", "")

    # Convert linking arc '‿' to word boundary space
    t = t.replace("‿", " ")

    # Convert sense-group slash '/' to natural comma pause
    t = re.sub(r"\s*/+\s*", ", ", t)
    t = re.sub(r"([.!?,;:])\s*,", r"\1", t)

    # Filter CJK characters and CJK punctuation
    t = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", " ", t)
    t = re.sub(
        r"[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]",
        " ",
        t,
    )

    # Clean remaining non-vocal symbols while keeping standard punctuation
    t = re.sub(r"[`~@#$%^&*()_+=\|\\<>{}\[\]\t]+", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"[ ]{2,}", " ", t).strip()
    return t


def sanitize_text(text: str) -> str:
    """Sanitize plain text lines while preserving line breaks."""
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        cleaned = clean_annotated_for_tts(line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def has_learning_annotations(text: str) -> bool:
    """Check if text contains visual learning markup or speaker labels."""
    if not text:
        return False
    patterns = [
        r"\*\*[^*]+\*\*",
        r"\*\([^)]+\)\*",
        r"\([a-zA-Z\']+\)",
        r"\[[^\]]+\]",
        r"‿",
        r"\s/\s",
        r"^\s*(?:[\*\-\•]\s*)?(?:\*{0,2})(?:W|M|Woman|Man)\s*:",
    ]
    return any(re.search(p, text, re.MULTILINE | re.IGNORECASE) for p in patterns)


def parse_learning_material(raw_input) -> dict:
    """
    Parse raw text or JSON payload into:
      - display_text: cleaned markdown/annotated lines for rich display
      - drills: list of {"phrase": str, "tip": str} for interactive 2-column Drill Cards
    """
    drills = []
    seen_drills = set()

    def add_drill(phrase: str, tip: str = ""):
        clean_p = clean_annotated_for_tts(phrase).strip(" ,.-:;")
        if not clean_p or not re.search(r"[a-zA-Z]", clean_p):
            return
        if len(clean_p) > 65:
            return
        key = clean_p.lower()
        if key not in seen_drills:
            seen_drills.add(key)
            clean_tip = tip.replace("`", "").strip()
            drills.append({"phrase": clean_p, "tip": clean_tip})

    # 1. Handle JSON payload if provided
    if isinstance(raw_input, dict):
        dialogue = raw_input.get("dialogue") or raw_input.get("text") or ""
        for item in raw_input.get("drills", []):
            if isinstance(item, dict):
                add_drill(item.get("phrase", ""), item.get("tip", ""))
            elif isinstance(item, str):
                add_drill(item, "")
        parsed = parse_learning_material(dialogue)
        merged_drills = drills + [
            d for d in parsed["drills"] if d["phrase"].lower() not in seen_drills
        ]
        return {"display_text": parsed["display_text"], "drills": merged_drills[:20]}

    text = str(raw_input or "").strip()
    if not text:
        return {"display_text": "", "drills": []}

    # Try parsing JSON string
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return parse_learning_material(obj)
        except Exception:
            pass

    # Strip trailing citation tags if copied from AI output
    text = re.sub(r"\[" + "cite" + r":[^\]]*\]", "", text)

    dialogue_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Skip Markdown headings, horizontal rules, and legend blockquotes
        if line.startswith("#") or re.match(r"^-{3,}$", line) or line.startswith(">"):
            continue

        # Markdown table row -> extract bold phrase & explanation into Drill Cards
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or all(re.match(r"^:?-+:?$", c) for c in cells if c):
                continue
            first_col = cells[0]
            if re.search(r"[\u4e00-\u9fff]", first_col):
                continue
            tip_parts = [c for c in cells[1:] if c]
            add_drill(first_col, " · ".join(tip_parts))
            continue

        # Dialogue line (W: / M:)
        if SPEAKER_PREFIX_RE.match(line):
            cleaned_diag = re.sub(r"^[\*\-\•]\s+", "", line)
            dialogue_lines.append(cleaned_diag)
            continue

        # Bullet list explaining a trap phrase (contains CJK + bold phrase)
        if re.match(r"^[\*\-\•]\s+", line) and re.search(r"[\u4e00-\u9fff]", line):
            bold_matches = re.findall(r"\*\*([^*]+)\*\*", line)
            if bold_matches:
                tip_text = re.sub(r"\*\*[^*]+\*\*", "", line).lstrip("*-• ：:").strip()
                for bm in bold_matches:
                    add_drill(bm, tip_text)
            continue

        # Normal English prose/dialogue line without W:/M:
        if re.search(r"[a-zA-Z]", line):
            cjk_count = len(re.findall(r"[\u4e00-\u9fff]", line))
            eng_count = len(re.findall(r"[a-zA-Z]", line))
            if cjk_count > eng_count:
                continue
            dialogue_lines.append(re.sub(r"^[\*\-\•]\s+", "", line))

    # Auto-extract notable linking/phonetic chunks from annotated dialogue if drills are sparse
    if len(drills) < 4 and dialogue_lines:
        for dline in dialogue_lines:
            for m in re.finditer(
                r"(?:[A-Za-z\*\(\)]+‿[A-Za-z\*\(\)‿]+|[A-Za-z\*\(\)]+\s*\[[^\]]+\])",
                dline,
            ):
                snippet = m.group(0)
                phon_m = re.search(r"\[([^\]]+)\]", snippet)
                tip = f"实际耳感: [{phon_m.group(1)}]" if phon_m else "连读 / 音变滑音"
                add_drill(snippet, tip)
                if len(drills) >= 12:
                    break

    # Fallback: if user pasted ONLY a table/drill list without dialogue text, use drill phrases
    if not dialogue_lines and drills:
        dialogue_lines = [d["phrase"] for d in drills]

    return {
        "display_text": "\n".join(dialogue_lines).strip(),
        "drills": drills[:20],
    }


def render_annotated_html(annotated_text: str, font_size: int = 18, is_dark: bool = False) -> str:
    """Render visual annotations into clean, high-readability HTML paragraphs."""
    if not annotated_text:
        return ""

    def token_replacer(match):
        if match.group("speaker"):
            raw_sp = match.group("speaker").replace("*", "").strip()
            is_female = raw_sp.upper().startswith(("W", "F"))
            bg = "rgba(225, 29, 72, 22)" if is_female else "rgba(194, 65, 12, 22)"
            fg = PALETTE_ROSE if is_female else PALETTE_ORANGE
            return (
                f'<span style="background-color:{bg}; color:{fg}; '
                f'font-weight:bold; padding:1px 7px; border-radius:5px;">'
                f"{raw_sp}</span>"
            )
        if match.group("weak"):
            inner = match.group("weak")[2:-2]
            return f'<span style="color:#A1A1AA; font-style:italic;">({inner})</span>'
        if match.group("bold"):
            inner = match.group("bold")[2:-2]
            b_fg = "#FFFFFF" if is_dark else PALETTE_BLACK
            b_bg = "rgba(244, 63, 94, 42)" if is_dark else "#FFF1F2"
            return (
                f'<span style="font-weight:bold; color:{b_fg}; '
                f'background-color:{b_bg}; border-radius:3px; padding:0px 2px;">{inner}</span>'
            )
        if match.group("phonetic"):
            val = match.group("phonetic")
            return (
                f'<span style="color:{PALETTE_ROSE}; font-size:{max(11, int(font_size * 0.78))}px; '
                f'font-weight:bold;">{val}</span>'
            )
        if match.group("silent"):
            val = match.group("silent")
            return f'<span style="color:#A1A1AA;">{val}</span>'
        if match.group("tie"):
            return f'<span style="color:{PALETTE_ORANGE}; font-weight:bold;">‿</span>'
        if match.group("slash"):
            return '<span style="color:#D4D4D8; font-weight:bold;"> │ </span>'
        return match.group(0)

    paragraphs = []
    for line in annotated_text.splitlines():
        if not line.strip():
            continue
        escaped = html.escape(line)
        styled_line = HTML_TOKEN_RE.sub(token_replacer, escaped)
        paragraphs.append(
            f'<p style="margin: 0px 0px 10px 0px; line-height: 1.58;">{styled_line}</p>'
        )

    return "".join(paragraphs)


def split_into_sentences(text: str) -> list:
    if not text:
        return []

    raw = text.strip()
    normalized = re.sub(r"[ \t]+", " ", raw)

    # Protect bracketed phonetics from internal splitting
    brackets = []

    def mask_bracket(m):
        brackets.append(m.group(0))
        return f"<BRK_{len(brackets) - 1}>"

    normalized = re.sub(r"\[[^\]]+\]", mask_bracket, normalized)
    normalized = re.sub(r"\.{3,}", "<ELLIPSIS>", normalized)
    normalized = re.sub(r"…", "<ELLIPSIS>", normalized)
    normalized = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", normalized)

    for abbr in ABBREVIATIONS:

        def replace_abbr(m):
            return m.group(0).replace(".", "<DOT>")

        normalized = re.sub(
            r"\b" + abbr, replace_abbr, normalized, flags=re.IGNORECASE
        )

    pattern = r"([.!?]+[\"\'”’\)\]]*)\s+"
    marked = re.sub(pattern, r"\1<SPLIT>", normalized)

    chunks = marked.split("<SPLIT>")
    sentences = []
    for s in chunks:
        s_clean = s.replace("<DOT>", ".").replace("<ELLIPSIS>", "...")
        for idx, brk in enumerate(brackets):
            s_clean = s_clean.replace(f"<BRK_{idx}>", brk)
        s_clean = s_clean.strip()
        if s_clean:
            sentences.append(s_clean)
    return sentences


def build_timeline_segments(visible_text: str) -> list:
    """
    Map visible text in QTextEdit into aligned sentence segments:
    Returns list of dicts:
      {"start_char": int, "end_char": int, "speaker": "F"|"M"|"DEFAULT", "tts_text": str}
    """
    segments = []
    if not visible_text:
        return segments

    offset = 0
    lines = visible_text.split("\n")
    for line_idx, line in enumerate(lines):
        line_len = len(line)
        if line.strip():
            speaker, _ = detect_speaker(line)
            sentences = split_into_sentences(line)
            search_cursor = 0
            for sent in sentences:
                pos = line.find(sent, search_cursor)
                if pos == -1:
                    pos = search_cursor
                end_pos = pos + len(sent)
                search_cursor = end_pos

                tts_clean = clean_annotated_for_tts(sent)
                if tts_clean and re.search(r"[a-zA-Z0-9]", tts_clean):
                    segments.append(
                        {
                            "start_char": offset + pos,
                            "end_char": offset + end_pos,
                            "speaker": speaker,
                            "tts_text": tts_clean,
                        }
                    )
        offset += line_len + (1 if line_idx < len(lines) - 1 else 0)

    return segments