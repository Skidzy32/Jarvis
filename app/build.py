#!/usr/bin/env python3
"""
build.py — Indexer for the Jarvis knowledge galaxy.

Scans every .md file under NOTES_DIR, builds a node per file, and links
nodes that mention each other's title or share a [[wikilink]]. Writes
viewer/graph-data.js as `const GRAPH = {nodes: [...], links: [...]}`.

Python 3, standard library only.
"""
import json
import os
import re
import sys

NOTES_DIR = sys.argv[1] if len(sys.argv) > 1 else "notes"
OUTPUT_PATH = os.path.join("viewer", "graph-data.js")
EXCERPT_LEN = 700

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def title_from_filename(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return name.replace("-", " ").replace("_", " ").strip().title()


def title_for_note(path, text):
    """
    Prefers the note's own '# Heading' as the title — this is what keeps a
    captured note's title readable (its content, not its date-stamped
    filename). Falls back to the filename when there's no heading.
    """
    match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if match:
        heading = match.group(1).strip()
        if heading:
            return heading
    return title_from_filename(path)


def group_from_path(path, root):
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "general"


def clean_excerpt(text):
    # Strip markdown noise for a cleaner excerpt, then trim to EXCERPT_LEN.
    text = re.sub(r"^#.*$", "", text, flags=re.MULTILINE)  # headers
    text = WIKILINK_RE.sub(lambda m: m.group(1), text)  # unwrap [[links]]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:EXCERPT_LEN]


def _load_records():
    records_dir = os.path.join(NOTES_DIR, ".jarvis", "records")
    recs = {}
    if os.path.isdir(records_dir):
        for fn in os.listdir(records_dir):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(records_dir, fn), "r", encoding="utf-8") as f:
                        r = json.load(f)
                    recs[r["id"]] = r
                except (OSError, ValueError, KeyError):
                    continue
    return recs


def unlinked_pairs(nodes):
    """3.6.0: pairs you said "unlink" to, as node-id pairs. No automatic
    link (title mention, shared words, sorting) may join them again."""
    recs = _load_records()
    index_by_rel = {os.path.relpath(n["path"], NOTES_DIR).replace(os.sep, "/"): n["id"] for n in nodes}
    out = set()
    for r in recs.values():
        a = index_by_rel.get(r.get("path"))
        for other in r.get("unlinked", []):
            t = recs.get(other)
            b = index_by_rel.get(t.get("path")) if t else None
            if a is not None and b is not None:
                out.add(tuple(sorted((a, b))))
    return out


def record_links(nodes, seen_pairs):
    records_dir = os.path.join(NOTES_DIR, ".jarvis", "records")
    if not os.path.isdir(records_dir):
        return []
    recs = {}
    for fn in os.listdir(records_dir):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(records_dir, fn), "r", encoding="utf-8") as f:
                    r = json.load(f)
                recs[r["id"]] = r
            except (OSError, ValueError, KeyError):
                continue   # a damaged record is reported by /diag, not here
    index_by_rel = {os.path.relpath(n["path"], NOTES_DIR).replace(os.sep, "/"): n["id"] for n in nodes}
    out = []
    for r in recs.values():
        if r.get("missing"):
            continue
        a = index_by_rel.get(r.get("path"))
        for l in r.get("links", []):
            if l.get("kind") != "related":
                continue
            target = recs.get(l.get("target_id"))
            b = index_by_rel.get(target.get("path")) if target and not target.get("missing") else None
            if a is None or b is None or a == b:
                continue
            pair = tuple(sorted((a, b)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            out.append({"source": pair[0], "target": pair[1],
                        "suggested": not (l.get("by") == "you" and l.get("confirmed")),
                        # 3.9.0: a note filed in a category "orbits" it (drawn close)
                        "orbit": l.get("reason") == "filed together"})
    return out


def write_graph(graph):
    os.makedirs("viewer", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("const GRAPH = ")
        f.write(json.dumps(graph, indent=2))
        f.write(";\n")


def main():
    if not os.path.isdir(NOTES_DIR):
        # 2.3.0: a fresh copy may have no notes folder at all (git doesn't
        # keep empty folders). Make it, and carry on to an empty galaxy.
        os.makedirs(NOTES_DIR, exist_ok=True)

    md_files = []
    for dirpath, dirnames, filenames in os.walk(NOTES_DIR):
        # 2.3.0: notes/.jarvis holds Jarvis's records and index, not notes.
        dirnames[:] = [d for d in dirnames if d != ".jarvis"]
        for fn in filenames:
            if fn.lower().endswith(".md"):
                md_files.append(os.path.join(dirpath, fn))
    md_files.sort()

    if not md_files:
        # 2.3.0: an empty notes folder is a real, normal state now (the demo
        # notes live in examples/ and your own record starts fresh). Write an
        # empty galaxy rather than failing -- failing here used to make the
        # very first "remember that..." impossible.
        write_graph({"nodes": [], "links": []})
        print(f"No notes yet under {NOTES_DIR} -> empty galaxy written to {OUTPUT_PATH}")
        return

    nodes = []
    raw_texts = []
    wikilinks_per_note = []

    for path in md_files:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        title = title_for_note(path, text)
        group = group_from_path(path, NOTES_DIR)
        excerpt = clean_excerpt(text)
        wikilinks = set(WIKILINK_RE.findall(text))

        nodes.append({
            "id": len(nodes),
            "label": title,
            "group": group,
            "excerpt": excerpt,
            "path": path,
        })
        raw_texts.append(text.lower())
        wikilinks_per_note.append(wikilinks)

    # Build links: title mention OR shared wikilink target.
    links = []
    seen_pairs = set(unlinked_pairs(nodes))   # 3.6.0: never re-join what you unlinked
    titles_lower = [n["label"].lower() for n in nodes]

    for i, node_i in enumerate(nodes):
        for j, node_j in enumerate(nodes):
            if i >= j:
                continue
            pair = (i, j)
            if pair in seen_pairs:
                continue

            connected = False

            # A mentions B's title, or vice versa (avoid trivial self-substring noise
            # by requiring the title be at least 3 chars).
            if len(titles_lower[j]) > 2 and titles_lower[j] in raw_texts[i]:
                connected = True
            elif len(titles_lower[i]) > 2 and titles_lower[i] in raw_texts[j]:
                connected = True

            # Shared wikilink target (both notes reference the same thing,
            # e.g. both link to [[Jamie Chen]]).
            if not connected and wikilinks_per_note[i] & wikilinks_per_note[j]:
                connected = True

            # A's wikilink target is literally B's title (direct reference).
            if not connected:
                wl_i_lower = {w.lower() for w in wikilinks_per_note[i]}
                wl_j_lower = {w.lower() for w in wikilinks_per_note[j]}
                if titles_lower[j] in wl_i_lower or titles_lower[i] in wl_j_lower:
                    connected = True

            if connected:
                links.append({"source": i, "target": j})
                seen_pairs.add(pair)

    # Link notes that genuinely share content, even weakly — but never
    # fabricate a connection where none exists. A standalone note is a
    # legitimate outcome, not a bug; the drift that makes an isolated note
    # fly off-screen is a rendering problem, handled in the viewer instead.
    linked_ids = set()
    for l in links:
        linked_ids.add(l["source"])
        linked_ids.add(l["target"])

    FALLBACK_STOPWORDS = {
        "the", "a", "an", "is", "are", "was", "were", "and", "or", "but",
        "to", "of", "in", "on", "for", "with", "this", "that", "it", "as",
        "at", "by", "from", "be", "been", "has", "had", "have", "will",
        "not", "no", "so", "we", "our", "us", "i", "you", "your", "note",
        # 2.5.0 fix: words from Jarvis's own "Captured 2026-... from paper.
        # Photo: ..." line. Every capture has them, so any two captures that
        # shared just one real word were being joined.
        "captured", "added", "from", "paper", "photo", "png", "jpg",
    }

    def content_words(node):
        raw = f"{node['label']} {node['excerpt']}".lower()
        # 3.9.0: Jarvis's own line on a project/category it made ("Added
        # 2026-09-23: a category you made in Jarvis.") is not shared content;
        # it was joining every two categories to each other.
        raw = re.sub(r"added \d{4}-\d{2}-\d{2}: a (?:category|project) you made in jarvis\.", " ", raw)
        return {
            w for w in re.findall(r"[a-z0-9]+", raw)
            if w not in FALLBACK_STOPWORDS and not w.isdigit()
        }

    word_sets = [content_words(n) for n in nodes]

    for i in range(len(nodes)):
        if i in linked_ids:
            continue
        best_j, best_score = None, 0
        for j in range(len(nodes)):
            if j == i:
                continue
            score = len(word_sets[i] & word_sets[j])
            if score > best_score:
                best_score, best_j = score, j
        # Only link if there's real, non-trivial overlap. No overlap means
        # no link — an isolated note stays isolated, honestly.
        if best_j is not None and best_score >= 2:
            pair = tuple(sorted((i, best_j)))
            if pair not in seen_pairs:
                links.append({"source": pair[0], "target": pair[1]})
                seen_pairs.add(pair)
            linked_ids.add(i)
            linked_ids.add(best_j)

    # 2.5.0: links Jarvis suggested while sorting ("both mention Dave"),
    # stored in notes/.jarvis/records -- never written into the notes.
    # Resolved by record id, so a note that moved keeps its links.
    links.extend(record_links(nodes, seen_pairs))

    graph = {"nodes": nodes, "links": links}
    write_graph(graph)

    print(f"Indexed {len(nodes)} notes, {len(links)} links -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
