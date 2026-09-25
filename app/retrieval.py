"""
retrieval.py — 4.6.0 (Jarvis 5.0, Phase 2): finding the right notes.

Before 4.6.0 a question was matched to notes by shared words in the title
and a short excerpt, and nothing else. Now every question goes through the
same layers (spec §7, §21), all on this computer (your choice: no AI
"embeddings", nothing extra sent anywhere):

  1. exact      the question names a note's title (or most of it)
  2. words      shared words in the title and the WHOLE note, forgiving about
                word forms ("invoices" = "invoice", "paying" = "pay")
  3. meaning    a small built-in list of words that mean much the same
                ("car" ~ "van", "mum" ~ "mother", "money" ~ "budget"), plus
                your own in notes/.jarvis/synonyms.json if you add one
  4. names      people, places, organisations and themes Jarvis read from a note
  5. related    notes linked to a strong match, and its category or project
  6. decisions  "why"/"should I"/"decide" questions lean towards decisions
  7. time       what's current comes before what's been replaced or is only
                historical; asking about the past ("previously", "used to")
                turns that round
  8. weight     importance, and a little for recent and recently-used notes

Each note that comes back carries its reasons ("title", "linked to X",
"historical"), so Jarvis can say why it chose it (spec principle 7), and the
brain is told which notes are only historical, so it can't mistake an old
preference for today's.

Standard library only.
"""

import datetime
import json
import os
import re

import knowledge
import records

TOP_K = 5
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "so", "of", "to", "in", "on", "at", "by", "for", "with",
    "about", "as", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "done", "have",
    "has", "had", "i", "me", "my", "mine", "we", "us", "our", "you", "your", "he", "him", "his", "she", "her",
    "it", "its", "they", "them", "their", "this", "that", "these", "those", "what", "which", "who", "whom",
    "when", "where", "why", "how", "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    "not", "no", "yes", "ok", "okay", "well", "just", "really", "very", "much", "lot", "want", "need", "like",
    "feel", "think", "know", "tell", "there", "here", "any", "some", "all", "from", "into", "up", "out", "please",
    "thanks", "thank", "hi", "hello", "hey", "jarvis", "sir", "madam", "note", "notes", "again", "also", "get",
    "got", "say", "said", "thing", "things", "anything", "something", "currently", "now", "previously", "used",
    "before", "these", "days", "still", "ever",
}
# words that mean much the same, both ways (kept short on purpose)
SYNONYM_GROUPS = [
    {"car", "van", "vehicle", "motor"}, {"mum", "mom", "mother"}, {"dad", "father"},
    {"money", "finance", "budget", "cost", "price", "spend", "spending"}, {"job", "work", "career", "employer"},
    {"doctor", "gp", "surgery", "appointment"}, {"house", "home", "flat"}, {"holiday", "trip", "travel", "vacation"},
    {"birthday", "bday"}, {"invoice", "bill", "payment"}, {"buy", "purchase", "order"},
    {"fix", "repair", "mend"}, {"meeting", "call", "catchup"}, {"idea", "thought"}, {"boss", "manager"},
    {"pc", "computer", "laptop"}, {"phone", "mobile"}, {"gym", "workout", "exercise", "training"},
    {"food", "meal", "dinner", "lunch", "breakfast"}, {"drink", "coffee", "tea"},
]
PAST_RE = re.compile(r"\b(previously|used to|before|back then|in the past|old|earlier|originally|history|did i (use to|think|prefer))\b", re.I)
NOW_RE = re.compile(r"\b(currently|now|these days|at the moment|today|still)\b", re.I)
DECISION_RE = re.compile(r"\b(why did i|decid\w*|decision|should i|chose|choose|choice|go with)\b", re.I)
# pleasantries: a question made only of these is small talk, not a search
SMALL_TALK = {"good", "morning", "evening", "afternoon", "night", "how", "going", "doing", "great", "fine", "day",
              "cheer", "cheers", "mate", "alright", "nice", "see", "later", "bye", "goodbye", "sup", "yo", "evening"}
_TEXT_CACHE = {}


def stem(w):
    """Forgiving word forms: plurals, possessives, -ing, -ed (light, English)."""
    w = re.sub(r"['’]s$", "", w.lower()).strip("'’")
    for suf, rep, keep in (("ies", "y", 4), ("ing", "", 5), ("ed", "", 4), ("es", "", 5), ("s", "", 3)):
        if len(w) > keep and w.endswith(suf) and not (suf == "s" and w.endswith("ss")):
            w = w[: -len(suf)] + rep
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":      # "stopp" -> "stop"
        w = w[:-1]
    return w


def words(text):
    return {stem(w) for w in re.findall(r"[a-z0-9'’]+", (text or "").lower()) if w not in STOPWORDS and len(w) > 1} - {""}


def _synonym_map(notes_dir=None):
    groups = [set(g) for g in SYNONYM_GROUPS]
    try:
        with open(os.path.join(records.store_dir(notes_dir), "synonyms.json"), encoding="utf-8") as f:
            extra = json.load(f)
        for g in extra if isinstance(extra, list) else []:
            if isinstance(g, list):
                groups.append({str(x).lower() for x in g})
    except (OSError, ValueError):
        pass
    out = {}
    for g in groups:
        stems = {stem(w) for w in g}
        for s in stems:
            out.setdefault(s, set()).update(stems - {s})
    return out


def _note(path):
    """(title, words) of a note, cached until the file changes."""
    try:
        m = os.path.getmtime(path)
    except OSError:
        return "", set()
    hit = _TEXT_CACHE.get(path)
    if hit and hit[0] == m:
        return hit[1], hit[2]
    try:
        text = records._read_text(path)
        title = records.title_of(path, text)
    except OSError:
        text, title = "", ""
    _TEXT_CACHE[path] = (m, title, words(text))
    return title, _TEXT_CACHE[path][2]


def mode_of(question):
    if PAST_RE.search(question or ""):
        return "past"
    if NOW_RE.search(question or ""):
        return "now"
    return "any"


def rank(question, notes_dir=None, top_k=TOP_K, today=None, types=None, min_ratio=0.5, related=2):
    """[(score, node, reasons)] best first, [] for small talk. types: only
    these node types (e.g. {"preference"}). min_ratio: keep notes scoring
    at least this share of the best (0 = any match). related: how many
    linked / same-category notes may come along as context even below that."""
    q_words = words(question)
    if not q_words or q_words <= {stem(w) for w in SMALL_TALK}:
        return []
    today = today or datetime.date.today()
    syn = _synonym_map(notes_dir)
    q_syn = set().union(*(syn.get(w, set()) for w in q_words)) - q_words
    mode = mode_of(question)
    decisionish = bool(DECISION_RE.search(question or ""))
    q_lower = (question or "").lower()
    recs, _ = records.load_all(notes_dir)
    refs = knowledge.load_referenced(notes_dir)
    live = {i: sc for i, sc in recs.items() if not sc.get("missing")}
    nodes = {}
    def node(rid):                     # worked out only for notes that matter
        if rid not in nodes:
            nodes[rid] = knowledge.node(live[rid], recs, notes_dir, refs, today)
        return nodes[rid]
    base = {}
    for rid, sc in live.items():
        title, all_words = _note(records.abs_path(sc["path"], notes_dir))
        reasons, score = [], 0.0
        t_words = words(title)
        if len(title) > 3 and title.lower() in q_lower:
            score += 10; reasons.append("named in your question")
        elif t_words and len(q_words & t_words) >= max(2, int(len(t_words) * 0.6 + 0.5)):
            score += 6; reasons.append("title")
        tw = len(q_words & t_words)
        if tw:
            score += 3 * tw
            if "title" not in reasons and "named in your question" not in reasons:
                reasons.append("title")
        body = all_words - t_words
        bw = len(q_words & body)
        if bw:
            score += bw; reasons.append("in the note")
        sw = len(q_syn & all_words)
        if sw:
            score += 0.6 * sw; reasons.append("similar words")
        v = (knowledge.reading(sc) or {}).get("value", {})
        tags = {stem(w) for f in ("themes", "people", "places", "organisations") for t in v.get(f, [])
                for w in re.findall(r"[a-z0-9]+", str(t).lower())}
        nw = len(q_words & tags)
        if nw:
            score += 2 * nw; reasons.append("names and themes")
        if score > 0:
            base[rid] = (score, reasons)
    if not base:
        return []
    # 5. related: linked notes and the same category/project, from strong matches only
    top = max(s for s, _ in base.values())
    spread = {}
    for rid, (s, _) in base.items():
        if s < top * 0.5:
            continue
        sc = live[rid]
        n = node(rid)
        for l in sc.get("links", []):
            tid = l.get("target_id")
            if tid in live and tid != rid:
                spread[tid] = max(spread.get(tid, (0, ""))[0], s * 0.35), f"linked to '{n['title']}'"
        group = sc.get("filed_under") or (sc.get("loop") or {}).get("project")
        if group:
            for oid, o in live.items():
                if oid != rid and (o.get("filed_under") == group or (o.get("loop") or {}).get("project") == group):
                    val = s * 0.2
                    if val > spread.get(oid, (0, ""))[0]:
                        spread[oid] = val, f"same {'category' if (live.get(group, {}).get('project_meta') or {}).get('kind') == 'category' else 'project'} as '{n['title']}'"
        if decisionish:
            for d in n["related_decisions"]:
                if d in live:
                    spread[d] = max(spread.get(d, (0, ""))[0], s * 0.5), f"a decision related to '{n['title']}'"
    scored = {}
    for rid in set(base) | set(spread):
        n = node(rid)
        if types and n["type"] not in types:
            continue
        s, reasons = base.get(rid, (0.0, []))
        reasons = list(reasons)
        if rid in spread:
            s += spread[rid][0]
            reasons.append(spread[rid][1])
        # 6. decisions
        if decisionish and n["type"] == "decision":
            s *= 1.5; reasons.append("a decision")
        # 7. time
        if not knowledge.is_current(n):
            if mode == "past":
                s *= 1.2
            else:
                s *= 0.15 if mode == "now" else 0.4
            reasons.append(n["state"])
        elif mode == "past":
            s *= 0.9
        # 8. weight
        s *= 1 + 0.1 * (n["importance"] - 1)
        age = knowledge._days_since(n["created"], today)
        if age is not None and 0 <= age <= 30:
            s *= 1.1
        used = knowledge._days_since(n["last_referenced"], today) if n["last_referenced"] else None
        if used is not None and used <= 7:
            s *= 1.05
        scored[rid] = (round(s, 3), n, reasons)
    ranked = sorted(scored.values(), key=lambda x: -x[0])
    if not ranked:
        return []
    best = ranked[0][0]
    keep = [r for r in ranked if r[0] >= max(0.5, best * min_ratio)][:top_k]
    kept = {r[1]["id"] for r in keep}
    context = [r for r in ranked if r[1]["id"] not in kept and r[1]["id"] in spread]
    return keep + context[:max(0, min(related, top_k + related - len(keep)))]


def relevant_graph_nodes(question, graph_nodes, notes_dir=None, top_k=TOP_K):
    """What /chat and /retrieve use: the galaxy's own node dicts, best first,
    each with "why" and "state" added. Same shape the old scoring returned."""
    by_path = {}
    root = notes_dir or records.NOTES_DIR
    for g in graph_nodes:
        p = g.get("path")
        if p:
            by_path[records.rel_path(p, root) if os.path.isabs(p) else p.replace(os.sep, "/")] = g
    out = []
    for score, n, reasons in rank(question, notes_dir, top_k):
        g = by_path.get(n["path"])
        if g is None:
            continue
        out.append(dict(g, why=reasons, state=n["state"], type=n["type"], current=knowledge.is_current(n),
                        origin=n["classification"]["origin"], confidence=n["classification"]["confidence"]))
    return out


def notes_block(nodes):
    """The NOTES section of the brain's prompt, labelled so a model can't
    treat a replaced or historical note as current."""
    parts = []
    for n in nodes:
        label = f"[Note {n['id']}] {n['label']}"
        tags = [n.get("type")] if n.get("type") else []
        if n.get("current") is False:
            tags.append(f"{n.get('state')}: NOT current, only what was true before")
        if n.get("origin") == "ai_inferred" and n.get("confidence") in ("uncertain", "needs_confirmation"):
            tags.append("Jarvis's reading of it is unconfirmed")
        if tags:
            label += " (" + "; ".join(t for t in tags if t) + ")"
        parts.append(f"{label}\n{n['excerpt']}")
    return "\n\n".join(parts)


# ---- "what do I currently prefer?" / "what did I previously think?" -------------

PREF_RE = re.compile(
    r"(?:what|which) (?:do|would) i (?:currently |now |actually )?(?:prefer|like best|favour|favor)(?: (?:about|for|when it comes to|in|with|regarding))?(?: (?P<t1>.+))?"
    r"|what (?:are|were) my (?:current |old |previous )?preferences?(?: (?:about|for|on|regarding))?(?: (?P<t2>.+))?"
    r"|what did i (?:previously |used to |originally )?(?:think|prefer|say|feel)(?: before)? (?:about|on|of) (?P<t3>.+?)(?: before| previously| originally)?"
    r"|how (?:do|did) i (?:currently |previously |used to )?feel about (?P<t4>.+)", re.I)
PREF_FILLER = re.compile(r"^(jarvis[, ]+|so[, ]+|ok[, ]+|okay[, ]+|hey[, ]+)+", re.I)


def preference_question(question):
    """(mode, topic) if the question asks what you prefer(red) or thought, else None."""
    q = PREF_FILLER.sub("", (question or "").strip())
    q = re.sub(r"[?.!]+$", "", q).strip()
    m = PREF_RE.fullmatch(q)
    if not m:
        return None
    topic = next((m.group(g) for g in ("t1", "t2", "t3", "t4") if m.group(g)), "") or ""
    topic = re.sub(r"\b(currently|now|these days|at the moment|previously|before)\b", "", topic, flags=re.I).strip()
    mode = "past" if PAST_RE.search(q) or re.search(r"\bwere my\b|\bdid i\b", q, re.I) else "now"
    return mode, topic


def answer_preferences(question, notes_dir=None, today=None):
    """{"spoken", "items"} or None. Only from your notes; says plainly when
    there's nothing, and keeps now and then apart."""
    pq = preference_question(question)
    if not pq:
        return None
    mode, topic = pq
    kinds = {"preference", "decision", "observation", "information", "idea", "reference"} if mode == "past" else {"preference"}
    if topic:
        hits = [(s, n) for s, n, _ in rank(topic, notes_dir, 8, today, kinds, min_ratio=0.0, related=0)]
    else:
        hits = [(1, n) for n in knowledge.all_nodes(notes_dir, today) if n["type"] == "preference"]
    now = [n for _, n in hits if knowledge.is_current(n) and n["type"] == "preference"]
    old = [n for _, n in hits if not knowledge.is_current(n)]
    about = f" about {topic}" if topic else ""
    def names(ns):
        return "; ".join(f"'{n['title']}'" + ("" if n["classification"]["confirmed"] or n["type_set_by"] == "you"
                                               else " (my reading, not confirmed)") for n in ns[:4])
    if mode == "now":
        if not now and not old:
            return {"spoken": f"I don't have a preference of yours recorded{about}, sir, and I won't guess one.", "items": []}
        parts = []
        if now:
            parts.append(f"What you currently prefer{about}: {names(now)}.")
        else:
            parts.append(f"Nothing current{about}, sir.")
        if old:
            parts.append(f"Earlier, since replaced: {names(old)}.")
        return {"spoken": " ".join(parts), "items": [{"title": n["title"], "record_id": n["id"]} for n in now + old][:8]}
    if not old:
        if now:
            return {"spoken": f"I have no older view of yours recorded{about}, sir; only the current one: {names(now)}.",
                    "items": [{"title": n["title"], "record_id": n["id"]} for n in now][:8]}
        return {"spoken": f"I have nothing recorded from before{about}, sir.", "items": []}
    spoken = f"Previously{about}: {names(old)}."
    if now:
        spoken += f" What's current now: {names(now)}."
    return {"spoken": spoken, "items": [{"title": n["title"], "record_id": n["id"]} for n in old + now][:8]}
