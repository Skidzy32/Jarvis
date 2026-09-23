"""Shared helpers for the sorting/loops tests: a scripted stand-in model
and throwaway notes. Not part of Jarvis itself."""
import hashlib
import json
import os

import records


def reading(**kw):
    base = {"home": "KNOWLEDGE", "area": None, "kinds": ["knowledge"], "intention": "none",
            "memory": "fact", "temporary": False, "people": [], "places": [], "organisations": [],
            "dates": [], "themes": [], "urgency": "unknown", "confidence": 0.9, "question": None}
    base.update(kw)
    return base


class StandIn:
    """Answers each note by matching words in its text to a script."""
    def __init__(self, script, model="stand-in/model-a"):
        self.script, self.model, self.calls, self.fail_on_call = script, model, 0, None
        self.raw_reply = None

    def __call__(self, config, messages):
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("Every model in the chain failed — test: HTTP 429")
        payload = json.loads(messages[1]["content"])
        self.last_payload = payload
        if self.raw_reply is not None:
            return self.raw_reply, self.model, None
        answers = []
        for note in payload["notes"]:
            for key, ans in self.script.items():
                if key in note["text"]:
                    answers.append(dict(ans, n=note["n"]))
                    break
        return "Here you go:\n```json\n" + json.dumps({"notes": answers}) + "\n```", self.model, None


def make_notes(root, texts):
    paths = []
    start = len(records.note_files(root))
    for i, text in enumerate(texts, start):
        p = os.path.join(root, "captures", f"n{i:02d}.md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {text[:40]}\n\nCaptured 2026-09-2{i % 3}.\n\n{text}\n")
        records.create_for_capture(p, text, source="typed", notes_dir=root)
        paths.append(p)
    return paths


def note_hashes(root):
    return {rel: hashlib.sha256(open(records.abs_path(rel, root), "rb").read()).hexdigest()
            for rel in records.note_files(root)}


def rec_for(root, path):
    return records.find_by_path(path, root)


