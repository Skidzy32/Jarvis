"""
capture.py — 5.0.0 (brief 3 §3-10): one way in for everything.

Every source ends up the same way: the ORIGINAL kept exactly as it arrived,
a plain note Jarvis can read and sort, and a record saying where it came
from. Nothing asks you to classify anything at capture time (§4).

  sources   typed, spoken, chat, clipboard, paper/photo/screenshot (the
            existing paper path), file, browser, email, calendar, app
  files     TXT MD CSV JSON -> read as text
            DOCX XLSX       -> text pulled from inside (standard library)
            PDF             -> best-effort text (standard library; a scanned
                               PDF has no text -- it says so and keeps the file)
            images          -> the photo path (read by a vision model, you
                               confirm the text)
            The file itself is kept byte for byte in notes/sources/, and ONE
            note stands for it (a file is a source, not hundreds of notes,
            §10). Long files: the note holds the first part and says so.
  states    RAW (not sorted yet) -> PROCESSED (sorted), or NEEDS
            CONFIRMATION (Jarvis wants your word) -- names over the inbox
            states Jarvis already had (§5).
  "where did you get that?" -> source_of(): what it came from, when, how.

Standard library only.
"""

import datetime
import hashlib
import html
import io
import json
import os
import re
import zipfile
import zlib

import records

SOURCES = ("typed", "spoken", "chat", "clipboard", "paper", "file", "browser", "email", "calendar", "app", "review",
           "person-note", "jarvis", "unknown")
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}
DOC_EXT = {".docx", ".xlsx", ".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_FILE_BYTES = 25 * 1024 * 1024
NOTE_TEXT_LIMIT = 30000          # characters of extracted text kept in the note itself
SOURCE_WORDS = {"typed": "you typed it", "spoken": "you said it", "chat": "from our conversation",
                "clipboard": "pasted from your clipboard", "paper": "from a photo of paper you confirmed",
                "file": "imported from a file", "browser": "sent from a web page", "email": "from an email",
                "calendar": "from your calendar", "app": "from a connected app", "review": "your answer in a review",
                "person-note": "a note about a person", "jarvis": "a follow-up Jarvis carries for you",
                "unknown": "origin not recorded"}


# ---- reading files --------------------------------------------------------------------

def _xml_text(xml, para_tag):
    xml = re.sub(rf"</{para_tag}>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    text = _xml_text(xml, "w:p")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _xlsx(data, max_rows=2000):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            for si in re.findall(r"<si>(.*?)</si>", sx, re.S):
                shared.append(html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
        sheets = sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        out, rows = [], 0
        for n in sheets:
            out.append(f"[{n.rsplit('/', 1)[1].replace('.xml', '')}]")
            sx = z.read(n).decode("utf-8", "ignore")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sx, re.S):
                cells = []
                for attrs, body in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                    v = re.search(r"<v>(.*?)</v>", body, re.S)
                    inline = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                    if 't="s"' in attrs and v:
                        i = int(v.group(1))
                        cells.append(shared[i] if i < len(shared) else "")
                    elif inline:
                        cells.append(html.unescape(inline.group(1)))
                    else:
                        cells.append(html.unescape(v.group(1)) if v else "")
                out.append("\t".join(cells).rstrip())
                rows += 1
                if rows >= max_rows:
                    out.append(f"(stopped after {max_rows} rows)")
                    return "\n".join(out).strip()
    return "\n".join(out).strip()


def _pdf_strings(chunk):
    """Text shown by a content stream: (..) in Tj / TJ / ' / \" operators."""
    out = []
    for m in re.finditer(rb"\[((?:\\.|[^\]])*)\]\s*TJ|(\((?:\\.|[^\\)])*\))\s*(?:Tj|'|\")|(T\*|Td|TD|ET)", chunk, re.S):
        if m.group(3):
            out.append(b"\n" if m.group(3) in (b"T*", b"ET") else b" ")
            continue
        parts = re.findall(rb"\((?:\\.|[^\\)])*\)", m.group(1) or m.group(2))
        for p in parts:
            s = p[1:-1]
            s = re.sub(rb"\\([nrtbf()\\])", lambda x: {b"n": b"\n", b"r": b"", b"t": b"\t", b"b": b"", b"f": b"",
                                                         b"(": b"(", b")": b")", b"\\": b"\\"}[x.group(1)], s)
            s = re.sub(rb"\\(\d{1,3})", lambda x: bytes([int(x.group(1), 8) & 255]), s)
            out.append(s)
    return b"".join(out)


def _pdf(data):
    texts = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        head = data[max(0, m.start() - 300):m.start()]
        if b"FlateDecode" in head:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                continue
        elif re.search(rb"/Filter", head):
            continue                                   # other encodings: not attempted
        if b"BT" in raw:
            texts.append(_pdf_strings(raw))
    text = b"\n".join(texts).decode("latin-1", "ignore")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract(filename, data):
    """(text, kind, quality) -- quality: full / partial / none / image."""
    ext = os.path.splitext(filename.lower())[1]
    if ext in IMAGE_EXT:
        return "", "image", "image"
    if ext in TEXT_EXT:
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return data.decode(enc).strip(), ext.lstrip("."), "full"
            except UnicodeDecodeError:
                continue
    try:
        if ext == ".docx":
            t = _docx(data)
            return t, "docx", "full" if t else "none"
        if ext == ".xlsx":
            t = _xlsx(data)
            return t, "xlsx", "full" if t else "none"
        if ext == ".pdf":
            t = _pdf(data)
            words = len(re.findall(r"[A-Za-z]{3,}", t))
            return t, "pdf", "partial" if words >= 5 else "none"
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return "", ext.lstrip("."), "none"
    raise ValueError(f"I can't read {ext or 'that kind of'} files yet: TXT, MD, CSV, PDF, DOCX, XLSX and pictures work")


# ---- keeping it -------------------------------------------------------------------------------

def _slug(s, n=6):
    return "-".join(re.findall(r"[a-z0-9]+", (s or "").lower())[:n]) or "source"


def save_file(filename, data, notes_dir=None, now=None, meta=None):
    """Keeps the file and writes ONE note that stands for it. Returns
    (record, note_path, info). Raises ValueError for an unreadable type or
    an oversized file; nothing is written then."""
    filename = os.path.basename(filename or "file")
    if not data:
        raise ValueError("that file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("that file is over 25 MB")
    text, kind, quality = extract(filename, data)
    if kind == "image":
        raise ValueError("pictures go through the photo reader")
    root = notes_dir or records.NOTES_DIR
    folder = os.path.join(root, "sources")
    os.makedirs(folder, exist_ok=True)
    now = now or datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    base, ext = os.path.splitext(filename)
    keep = os.path.join(folder, f"{stamp}-{_slug(base)}{ext.lower()}")
    note = os.path.join(folder, f"{stamp}-{_slug(base)}.md")
    n = 1
    while os.path.exists(keep) or os.path.exists(note):
        n += 1
        keep = os.path.join(folder, f"{stamp}-{n}-{_slug(base)}{ext.lower()}")
        note = os.path.join(folder, f"{stamp}-{n}-{_slug(base)}.md")
    with open(keep, "wb") as f:
        f.write(data)
    shown = text[:NOTE_TEXT_LIMIT]
    cut = len(text) > NOTE_TEXT_LIMIT
    what = {"full": "", "partial": " The text was pulled out of the PDF as well as possible; layout, tables and "
                                   "pictures may be missing, so check the original for anything important.",
            "none": " No text could be read from it (a scanned PDF or an empty file); the original is kept."}[quality]
    body = (f"# {base}\n\nImported {now.date().isoformat()} from the file {filename}. "
            f"Original kept as {os.path.basename(keep)}.{what}"
            + (f" This note holds the first {NOTE_TEXT_LIMIT:,} characters of {len(text):,}; the rest is in the original." if cut else "")
            + f"\n\n{shown}\n")
    with open(note, "w", encoding="utf-8") as f:
        f.write(body)
    capture = {"source": "file", "filename": filename, "kind": kind, "quality": quality, "chars": len(text),
               "truncated": cut, **(meta or {})}
    sc = records.create_for_capture(
        note, f"[file] {filename}", source="file", notes_dir=root,
        extra={"source_file": records.rel_path(keep, root), "source_file_sha256": hashlib.sha256(data).hexdigest(),
               "capture": capture},
        extra_event={"how": "file import", "kind": kind, "quality": quality})
    return sc, note, capture


def record_meta(sc, source, meta, notes_dir=None):
    """Adds the capture details (clipboard / browser url / an external id)
    to a note's record after the note is written."""
    if not sc:
        return sc
    clean = {k: v for k, v in (meta or {}).items() if k in ("url", "title", "external_id", "app", "selection")
             and isinstance(v, str)}
    with records._LOCK:
        cur = records.load_all(notes_dir)[0].get(sc["id"]) or sc
        cur["capture"] = dict(cur.get("capture") or {}, source=source, **{k: v[:2000] for k, v in clean.items()})
        records._event(cur, "capture details", source=source, **{k: v[:200] for k, v in clean.items()})
        records.save(cur, notes_dir)
        return cur


# ---- states and provenance -------------------------------------------------------------------

def state_of(sc):
    """RAW / PROCESSED / NEEDS CONFIRMATION (brief 3 §5), over the inbox states."""
    st = sc.get("status")
    if st == "sorted":
        return "PROCESSED"
    if st in ("needs_clarification", "unplaced"):
        return "NEEDS CONFIRMATION"
    return "RAW"


def _when(iso):
    try:
        d = datetime.datetime.fromisoformat(str(iso))
        return d.strftime("%-d %B %Y") if os.name != "nt" else d.strftime("%#d %B %Y")
    except (ValueError, TypeError):
        return str(iso or "an unknown date")[:10]


def source_of(sc, notes_dir=None):
    """Plain-English provenance for 'where did you get that?'."""
    import knowledge
    title = knowledge._title(sc, notes_dir)
    cap = sc.get("capture") or {}
    src = cap.get("source") or sc.get("source") or "unknown"
    how = SOURCE_WORDS.get(src, src)
    parts = [f"'{title}': {how}, on {_when(sc.get('created'))}"]
    if cap.get("filename"):
        parts.append(f"from the file {cap['filename']} (kept in notes/{sc.get('source_file', 'sources')})")
    elif sc.get("source_file"):
        parts.append(f"the original is kept as notes/{sc['source_file']}")
    if cap.get("url"):
        parts.append(f"from {cap.get('title') or 'a web page'} ({cap['url']})")
    if cap.get("external_id"):
        parts.append(f"external id {cap['external_id']}")
    r = knowledge.reading(sc)
    if r:
        parts.append("how it's filed is " + ("confirmed by you" if r.get("confirmed") else
                                             f"Jarvis's reading ({knowledge.confidence_word(r.get('confidence'))}), not confirmed"))
    return "; ".join(parts) + "."
