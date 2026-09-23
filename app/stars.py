"""
stars.py — 3.8.0: what the right-click menu on a star does.

  rename star            changes the note's title line ("# ...")
  change star contents   replaces the note's text with what you typed
  link star              links it to another star (right-click the second)
  put in category        files it (or a group of stars) under a category
  take out of category
  delete star            moves the note to Jarvis's bin: notes/.jarvis/bin/

These are YOUR edits, so they do change notes -- but nothing is ever lost:
  - before a note is renamed or edited, its previous text is kept in
    notes/.jarvis/versions/<record id>/<time>.md;
  - "delete" moves the note to notes/.jarvis/bin/, never deletes it;
  - every one of these returns an Undo that puts the note and its record
    back exactly (actions.py).
Renaming a category's star renames the category.

Only notes inside the notes folder (not Jarvis's own .jarvis folder) can be
touched; anything else is refused. Standard library only.
"""

import datetime
import os
import re
import shutil

import actions
import links
import organise
import records
import reviews

TITLE_LINE = re.compile(r"^#\s+.*$", re.M)


def _note(path, notes_dir):
    """The record for a star's note, after checking it really is a note in
    the notes folder."""
    root = os.path.realpath(notes_dir or records.NOTES_DIR)
    real = os.path.realpath(path or "")
    rel = os.path.relpath(real, root)
    if rel.startswith("..") or rel.split(os.sep)[0] == records.STORE_DIRNAME or not real.endswith(".md") \
            or not os.path.isfile(real):
        raise ValueError("that isn't one of your notes")
    sc = records.find_by_path(real, notes_dir)
    if sc is None:
        records.sync(notes_dir)
        sc = records.find_by_path(real, notes_dir)
    if sc is None:
        raise ValueError("that note has no record yet; try again in a moment")
    return real, sc


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _keep_version(real, sc, notes_dir):
    folder = os.path.join(records.store_dir(notes_dir), "versions", sc["id"])
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"{_stamp()}.md")
    shutil.copy2(real, dest)
    return dest


def _write(real, text):
    with open(real, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _refingerprint(real, event, notes_dir, **detail):
    with records._LOCK:
        sc = records.find_by_path(real, notes_dir)
        sc["content_sha256"] = records.sha256_file(real)
        records._event(sc, event, **detail)
        records.save(sc, notes_dir)


def rename(path, title, notes_dir=None):
    title = re.sub(r"\s+", " ", (title or "").strip())[:120]
    if not title:
        raise ValueError("a star needs a name")
    real, sc = _note(path, notes_dir)
    before = actions.begin(notes_dir)
    old_bytes = open(real, "rb").read()
    old_title = reviews._title(sc, notes_dir)
    text = old_bytes.decode("utf-8", errors="replace")
    kept = _keep_version(real, sc, notes_dir)
    text = TITLE_LINE.sub(lambda m: f"# {title}", text, count=1) if TITLE_LINE.search(text) else f"# {title}\n\n{text}"
    _write(real, text)
    _refingerprint(real, "renamed by you", notes_dir, old=old_title, new=title, previous_version=kept)
    token = actions.commit(before, notes_dir, [{"path": real, "old": old_bytes, "remove": kept}])
    kind = (sc.get("project_meta") or {}).get("kind") or ("project" if sc.get("project_meta") else "star")
    return f"Renamed the {kind} '{old_title}' to '{title}', sir.", token


def read(path, notes_dir=None):
    real, sc = _note(path, notes_dir)
    return {"title": reviews._title(sc, notes_dir), "text": open(real, encoding="utf-8", errors="replace").read()}


def edit(path, text, notes_dir=None):
    real, sc = _note(path, notes_dir)
    if text is None or not str(text).strip():
        raise ValueError("that would leave the note empty (use Delete star instead)")
    before = actions.begin(notes_dir)
    old_bytes = open(real, "rb").read()
    if old_bytes.decode("utf-8", errors="replace") == text:
        return "No changes, sir.", None
    kept = _keep_version(real, sc, notes_dir)
    _write(real, text)
    _refingerprint(real, "edited by you in Jarvis", notes_dir, previous_version=kept)
    token = actions.commit(before, notes_dir, [{"path": real, "old": old_bytes, "remove": kept}])
    return f"Saved your changes to '{records.title_of(real)}', sir. The previous version is kept.", token


def delete(path, notes_dir=None):
    real, sc = _note(path, notes_dir)
    title = reviews._title(sc, notes_dir)
    before = actions.begin(notes_dir)
    if sc.get("project_meta"):          # a category/project: its notes stay, just unfiled
        recs, _ = records.load_all(notes_dir)
        organise.unfile([m for m in recs.values() if m.get("filed_under") == sc["id"]], notes_dir)
    old_bytes = open(real, "rb").read()
    folder = os.path.join(records.store_dir(notes_dir), "bin")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"{_stamp()}-{os.path.basename(real)}")
    shutil.move(real, dest)
    with records._LOCK:
        sc = records.load_all(notes_dir)[0][sc["id"]]
        sc["missing"] = True
        sc["deleted"] = {"at": records.now_iso(), "bin": os.path.relpath(dest, notes_dir or records.NOTES_DIR).replace(os.sep, "/")}
        records._event(sc, "deleted by you (moved to Jarvis's bin)", bin=sc["deleted"]["bin"])
        records.save(sc, notes_dir)
    token = actions.commit(before, notes_dir, [{"path": real, "old": old_bytes, "remove": dest}])
    return f"Deleted '{title}', sir. It's in Jarvis's bin if you ever want it back.", token


def link(path_a, path_b, notes_dir=None):
    ra, a = _note(path_a, notes_dir)
    rb, b = _note(path_b, notes_dir)
    if a["id"] == b["id"]:
        raise ValueError("that's the same star")
    before = actions.begin(notes_dir)
    items = [{"id": a["id"], "title": reviews._title(a, notes_dir)}, {"id": b["id"], "title": reviews._title(b, notes_dir)}]
    line = links.apply("link", items, notes_dir)
    return line, actions.commit(before, notes_dir)


def categorise(paths, name, notes_dir=None):
    name = re.sub(r"\s+", " ", (name or "").strip())[:60]
    if not name:
        raise ValueError("a category needs a name")
    items = []
    for p in paths:
        _r, sc = _note(p, notes_dir)
        if sc.get("project_meta"):
            continue                    # a category isn't filed inside another
        items.append({"id": sc["id"], "title": reviews._title(sc, notes_dir)})
    if not items:
        raise ValueError("pick at least one note")
    before = actions.begin(notes_dir)
    title, new, _id, kind = organise.file_under(name, items, notes_dir, "category")
    names = [f"'{i['title']}'" for i in items]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Put {who} in {'a new ' + kind + ', ' if new else 'the ' + kind + ' '}'{title}', sir.", actions.commit(before, notes_dir)


def uncategorise(paths, notes_dir=None):
    items = [{"id": _note(p, notes_dir)[1]["id"]} for p in paths]
    before = actions.begin(notes_dir)
    n = organise.unfile(items, notes_dir)
    if not n:
        return "That wasn't in a category, sir.", None
    return f"Taken out of {'its category' if n == 1 else 'their categories'}, sir.", actions.commit(before, notes_dir)


def info(path, notes_dir=None):
    """What the menu needs to show: title, category, whether it is one."""
    real, sc = _note(path, notes_dir)
    recs, _ = records.load_all(notes_dir)
    cat = recs.get(sc.get("filed_under") or "")
    return {"title": reviews._title(sc, notes_dir),
            "category": reviews._title(cat, notes_dir) if cat else None,
            "is_category": bool(sc.get("project_meta"))}
