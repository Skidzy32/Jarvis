"""
inbox.py — Personal OS Phase 2 (2.4.0): the INBOX, paper notes, and
Jarvis's own follow-ups.

Everything you capture lands in the INBOX first (spec §2: "capture first,
organise second"). Nothing here sorts anything -- that's Phase 3. This
file only:

  - lists what's in the inbox (records with home INBOX, still unprocessed)
  - saves a PAPER note once you've confirmed what the page says: the photo
    is kept as the original, your confirmed text becomes the note, and the
    AI's reading is stored beside it as an interpretation (which model,
    when, whether you corrected it) -- never in place of the photo
  - adds follow-ups you asked Jarvis to carry (F1: the privacy check) as
    real inbox items, once each

It reads and writes through records.py; it never rewrites an existing note.
Standard library only.
"""

import base64
import datetime
import hashlib
import json
import os
import re

import records

# ---- the inbox ---------------------------------------------------------------

FIRST_LINES_SKIP = re.compile(r"^(#\s|Captured \d{4}-\d{2}-\d{2}|Added \d{4}-\d{2}-\d{2})")


def _preview(text, limit=160):
    """The note's own words, without its title and date line."""
    lines = [ln.strip() for ln in text.splitlines()]
    body = [ln for ln in lines if ln and not FIRST_LINES_SKIP.match(ln)]
    joined = " ".join(body)
    return joined if len(joined) <= limit else joined[: limit - 1].rstrip() + "…"


def items(notes_dir=None):
    """Inbox items, newest first."""
    recs, _ = records.load_all(notes_dir)
    out = []
    for sc in recs.values():
        if sc.get("missing") or sc.get("home") != "INBOX" or sc.get("status") != "unprocessed":
            continue
        try:
            text = records._read_text(records.abs_path(sc["path"], notes_dir))
        except OSError:
            continue
        out.append({
            "id": sc["id"],
            "title": records.title_of(sc["path"], text),
            "preview": _preview(text),
            "created": sc.get("created"),
            "created_from": sc.get("created_from"),
            "source": sc.get("source"),
            "path": sc["path"],
            "photo": sc.get("source_file"),
        })
    out.sort(key=lambda i: i["created"] or "", reverse=True)
    return out


def overview(notes_dir=None, sorted_limit=30):
    """2.5.0: everything the inbox panel shows -- waiting to be sorted,
    questions Jarvis has for you, notes it couldn't place, and what it
    recently sorted (with where, why, and whether you've confirmed)."""
    recs, _ = records.load_all(notes_dir)
    out = {"waiting": items(notes_dir), "needs_you": [], "unsorted": [], "sorted": []}
    for sc in recs.values():
        status = sc.get("status")
        if sc.get("missing") or status not in ("needs_clarification", "unsorted", "sorted"):
            continue
        try:
            text = records._read_text(records.abs_path(sc["path"], notes_dir))
        except OSError:
            continue
        reading = next((i for i in reversed(sc.get("interpretations", [])) if i.get("kind") == "sorting"), None)
        v = reading["value"] if reading else {}
        entry = {
            "id": sc["id"], "title": records.title_of(sc["path"], text), "preview": _preview(text),
            "created": sc.get("created"), "source": sc.get("source"), "path": sc["path"],
            "home": sc.get("home"), "suggested_home": v.get("home"), "kinds": v.get("kinds", []),
            "area": v.get("area"), "temporary": v.get("temporary", False),
            "confirmed": bool(reading and reading.get("confirmed")),
            "model": reading.get("model") if reading else None,
            "confidence": v.get("confidence"),
            "question": sc.get("question"),
            "related": [l.get("reason") for l in sc.get("links", []) if l.get("kind") == "related"][:3],
            "sorted_at": reading.get("at") if reading else None,
        }
        key = {"needs_clarification": "needs_you", "unsorted": "unsorted", "sorted": "sorted"}[status]
        out[key].append(entry)
    for k in ("needs_you", "unsorted", "sorted"):
        out[k].sort(key=lambda e: e["sorted_at"] or "", reverse=True)
    out["sorted"] = out["sorted"][:sorted_limit]
    return out


COUNT_WORDS = {1: "One thing", 2: "Two things", 3: "Three things", 4: "Four things",
               5: "Five things", 6: "Six things", 7: "Seven things", 8: "Eight things",
               9: "Nine things", 10: "Ten things"}


def spoken_summary(inbox_items):
    """What Jarvis says for "what's in my inbox?"."""
    n = len(inbox_items)
    if n == 0:
        return "Your inbox is empty, sir."
    head = COUNT_WORDS.get(n, f"{n} things")
    titles = [f"'{i['title']}'" for i in inbox_items[:3]]
    if n == 1:
        return f"One thing in your inbox, sir: {titles[0]}."
    listed = ", ".join(titles[:-1]) + f" and {titles[-1]}" if len(titles) > 1 else titles[0]
    more = "" if n <= 3 else f", and {n - 3} more"
    return (f"{head} in your inbox, sir. The newest: {listed}{more}. "
            "Sorting them is still to come; for now they wait there, safe.")


# ---- paper -------------------------------------------------------------------

PAPER_SYSTEM_PROMPT = (
    "You are transcribing a photo of a handwritten or printed page for its owner's "
    "personal notes. Write out exactly what is written, word for word, in the order it "
    "appears, keeping the writer's own spelling, slang, swearing, abbreviations and "
    "punctuation. Do not correct, summarise, soften, tidy up, interpret or add anything. "
    "Keep line breaks and list items. If a word can't be read, write [illegible]; if "
    "you're unsure of a word, write your best guess followed by [?]. Describe drawings, "
    "arrows or diagrams briefly in square brackets, e.g. [arrow from 'rent' to 'budget']. "
    "If crossed-out text is readable, write it as [crossed out: ...]. If there is no "
    "writing at all, reply only: [no writing found]. Reply with the transcription only."
)
PAPER_USER_PROMPT = "Transcribe this page exactly."

MAX_PHOTO_BYTES = 25 * 1024 * 1024

# Formats a browser can show and a vision model can read. Checked by the
# file's own first bytes, not by what the upload claims to be.
IMAGE_SIGNATURES = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


def image_kind(data):
    for sig, ext in IMAGE_SIGNATURES:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def decode_photo(b64):
    """(bytes, extension) or raises ValueError with a plain reason."""
    if not b64:
        raise ValueError("no photo was received")
    if "," in b64[:100] and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError):
        raise ValueError("the photo didn't arrive intact")
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError(f"the photo is over {MAX_PHOTO_BYTES // (1024 * 1024)} MB")
    ext = image_kind(data)
    if ext is None:
        raise ValueError("that file isn't a JPG, PNG, WebP or GIF picture "
                         "(iPhone HEIC photos need saving as JPG first)")
    return data, ext


def _title_from(text):
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith("["):
            return line[:80].rstrip(".")
    return "Paper note"


def save_paper(photo_b64, final_text, extracted_text=None, model_used=None,
               notes_dir=None, now=None):
    """
    Saves a confirmed paper note:
      notes/paper/<stamp>.<ext>       the photo, byte for byte as uploaded
      notes/paper/<stamp>-<slug>.md   the text you confirmed
      + its record: source "paper", the photo's path and fingerprint, your
        confirmed text as the original input, and the AI's reading as an
        interpretation (model, time, confirmed, whether you changed it).
    Returns (record, note_path). Raises ValueError for a bad photo or empty
    text -- nothing is written in that case.
    """
    data, ext = decode_photo(photo_b64)
    final_text = (final_text or "").strip()
    if not final_text:
        raise ValueError("there's no text to save; type what the page says, or discard it")
    root = notes_dir or records.NOTES_DIR
    folder = os.path.join(root, "paper")
    os.makedirs(folder, exist_ok=True)
    now = now or datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    title = _title_from(final_text)
    slug = "-".join(re.findall(r"[a-z0-9]+", title.lower())[:6]) or "page"

    photo_path = os.path.join(folder, f"{stamp}.{ext}")
    note_path = os.path.join(folder, f"{stamp}-{slug}.md")
    n = 1
    while os.path.exists(photo_path) or os.path.exists(note_path):
        n += 1
        photo_path = os.path.join(folder, f"{stamp}-{n}.{ext}")
        note_path = os.path.join(folder, f"{stamp}-{n}-{slug}.md")

    with open(photo_path, "wb") as f:
        f.write(data)
    photo_rel = records.rel_path(photo_path, root)
    body = (f"# {title}\n\nCaptured {now.date().isoformat()} from paper. "
            f"Photo: {os.path.basename(photo_path)}\n\n{final_text}\n")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(body)

    interpretations = []
    if extracted_text is not None:
        interpretations.append({
            "kind": "transcription",
            "value": extracted_text,
            "model": model_used,
            "at": records.now_iso(),
            "confidence": None,   # the models don't report one; not invented
            "confirmed": True,    # you read it and pressed Save
            "corrected_by_you": extracted_text.strip() != final_text,
        })
    sc = records.create_for_capture(
        note_path, final_text, source="paper", notes_dir=root,
        extra={"source_file": photo_rel,
               "source_file_sha256": hashlib.sha256(data).hexdigest(),
               "interpretations": interpretations},
        extra_event={"how": "paper", "read_by": model_used or "you typed it"},
    )
    return sc, note_path


# ---- follow-ups Jarvis carries for you -----------------------------------------

FOLLOWUPS = {
    "F1": {
        "slug": "check-openrouter-privacy-settings",
        "title": "Check OpenRouter's privacy settings before sending personal writing",
        "logged": "2026-09-22",
        # Your own words, from the build conversation where you asked for this.
        "said": ("I'm not too worried about the privacy right now but add it as a note to "
                 "follow up on that Jarvis can action and remind me about once completed."),
        "text": ("Before journal-style or emotional writing goes through Jarvis's brain "
                 "regularly, look at your OpenRouter privacy settings: whether the providers "
                 "behind the free models may keep or train on what's sent. That setting lives "
                 "in your OpenRouter account, so it's yours to check and change; Jarvis can't "
                 "do it for you. Once you've looked, tell Jarvis what you decided and why."),
    },
    "F2": {
        "slug": "work-out-image-reading",
        "title": "Work out a good solution for reading and processing images",
        "logged": "2026-09-23",
        "said": ("lets add a note or reminder somewhere for me to work out a good solution for "
                 "image reading and processing"),
        "text": ("Paper notes are read by whichever free model gets picked, and it may not be able "
                 "to read images at all. Things to decide: which model reads pictures (one fixed "
                 "vision model for this, or a short list of vision-capable ones to rotate "
                 "between); how well each copes with your handwriting (try a few real pages); "
                 "whether Windows' own offline text reader is good enough as a first pass or a "
                 "fallback; privacy, since photos of your writing leave the computer when a cloud "
                 "model reads them (see the OpenRouter privacy check); and the practical side: "
                 "several pages at once, photos from your phone, iPhone photo formats. The full "
                 "list is proposal P4 in the project's feature proposals."),
    },
}


def _seeded_path(root):
    return os.path.join(records.store_dir(root), "followups.json")


def seed_followups(notes_dir=None, personal=True):
    """Adds each follow-up to the inbox ONCE. If you later delete or move
    the note, it's not re-added -- that was your choice. Returns the ids
    added this time.
    4.0.0: F1 and F2 are the owner's own to-dos ("added at your request").
    The server no longer seeds them (personal=False), so a copy shared with
    someone else starts with an empty inbox; the owner's install has them
    already and nothing is removed."""
    if not personal:
        return []
    root = notes_dir or records.NOTES_DIR
    path = _seeded_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            seeded = json.load(f)
    except (OSError, ValueError):
        seeded = {}
    added = []
    for fid, fu in FOLLOWUPS.items():
        if fid in seeded:
            continue
        folder = os.path.join(root, "captures")
        os.makedirs(folder, exist_ok=True)
        note_path = os.path.join(folder, f"{fu['slug']}-{fu['logged']}.md")
        if not os.path.exists(note_path):
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(f"# {fu['title']}\n\nAdded {fu['logged']} at your request "
                        f"(follow-up {fid}).\n\n{fu['text']}\n")
            records.create_for_capture(
                note_path, fu["said"], source="jarvis", notes_dir=root,
                extra={"followup": fid},
                extra_event={"how": f"follow-up {fid} you asked Jarvis to carry",
                             "your_words_said": f"while building Jarvis, {fu['logged']}"})
        seeded[fid] = records.now_iso()
        added.append(fid)
    if added:
        records._atomic_write_json(path, seeded)
    return added
