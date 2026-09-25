"""
4.9.0: the optional stronger AI -- off by default; Claude Code on this PC,
an Anthropic API key, or a paid OpenRouter model; for harder questions and/or
as a rescue when every free model fails; with a daily cap. Stand-ins for
Claude Code, the Anthropic API and OpenRouter; throwaway folders; your real
config and notes are never touched. Run: python3 test_premium.py
"""
import io, json, os, shutil, tempfile, urllib.error
import models, premium, server, setup_flow

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


N = tempfile.mkdtemp(prefix="jarvis-prem-")
FREE = {"prompt": "0", "completion": "0"}
LIST = {"data": [{"id": "free/general:free", "name": "General", "pricing": FREE, "context_length": 32000,
                  "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                  "supported_parameters": ["reasoning"]}]}
models.refresh(N, fetch=lambda: LIST)

print("Test 1: off by default, for everyone")
check("no settings -> off", premium.settings({})["mode"] == "off")
check("off -> nothing added to the pool", premium.profile({}, "REASONING", N) == [] and premium.rescue_id({}, N) is None)
for bad in ({"mode": "gpt-99"}, {"model": "has spaces"}, {"daily_cap": "lots"}):
    try:
        premium.validate_changes(bad); ok = False
    except ValueError:
        ok = True
    check(f"refused: {bad}", ok)

print("Test 2: Settings -- the key is saved, never shown back")
C = os.path.join(N, "config.json")
json.dump({"openrouter_api_key": "sk-or-v1-" + "ab" * 32}, open(C, "w"))
setup_flow.save(C, {"premium": {"mode": "anthropic_api", "daily_cap": 3}, "anthropic_api_key": "sk-ant-" + "x" * 30})
st = setup_flow.state(C, "PUT-YOUR-KEY-HERE", N)
check("saved: mode, cap, key present", st["premium"]["mode"] == "anthropic_api" and st["premium"]["daily_cap"] == 3
      and st["premium"]["has_anthropic_key"])
check("the key never comes back out", "sk-ant-" not in json.dumps(st))
check("the OpenRouter key is untouched", json.load(open(C))["openrouter_api_key"].startswith("sk-or-v1-"))
try:
    setup_flow.save(C, {"anthropic_api_key": "sk-or-v1-wrong"}); ok = False
except ValueError:
    ok = True
check("a non-Anthropic key is refused", ok)

print("Test 3: Claude Code on this PC")
cfg = {"openrouter_api_key": "k", "premium": {"mode": "claude_code", "daily_cap": 2, "use_for": ["reasoning", "rescue"]}}
real_path = premium.claude_code_path
premium.claude_code_path = lambda: None
check("not installed -> not used, and says why", premium.available(cfg, N) == (False, "Claude Code isn't installed on this computer"))
premium.claude_code_path = lambda: "/usr/bin/claude"
check("installed -> in the pool for harder questions only", premium.profile(cfg, "REASONING", N)[0]["id"] == "claude-code"
      and premium.profile(cfg, "GENERAL", N) == [])
seen = {}
class R:  # a finished process
    def __init__(self, out, code=0): self.stdout, self.stderr, self.returncode = out, "", code
def fake_run(cmd, input, capture_output, text, timeout, cwd, creationflags):
    seen.update(cmd=cmd, input=input, cwd=cwd, files=os.listdir(cwd))
    return R(json.dumps({"type": "result", "is_error": False, "result": "Keep the van, sir; repairs are cheap."}))
msgs = [{"role": "system", "content": "BRIEF: You are Jarvis..."}, {"role": "user", "content": "should I sell the van?"}]
ans, used = premium.call(cfg, msgs, run=fake_run)
check("runs `claude -p` (print mode, JSON out)", seen["cmd"][1:4] == ["-p", "--output-format", "json"])
check("the brief and the question go in on stdin", "BRIEF: You are Jarvis" in seen["input"] and "should I sell the van?" in seen["input"])
check("in an empty folder (nothing of yours to look at), cleaned up after", seen["files"] == [] and not os.path.exists(seen["cwd"]))
check("its answer is used", ans == "Keep the van, sir; repairs are cheap." and used == "claude-code")
try:
    premium.call(cfg, msgs, run=lambda *a, **k: R(json.dumps({"is_error": True, "result": "Not logged in"})))
    ok = False
except RuntimeError as e:
    ok = "Not logged in" in str(e)
check("a Claude Code error comes back as a plain reason", ok)

print("Test 4: in the router")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "_call_openrouter_once", "runtime_override_model")}
real_call = premium.call
server.NOTES_DIR, server.runtime_override_model = N, None
asked = []
def free(api_key, model, messages):
    asked.append(model)
    return "Free answer, sir.", model
server._call_openrouter_once = free
premium.call = lambda config, messages, **k: (asked.append("PREMIUM"), ("Premium answer, sir.", premium.model_id(premium.settings(config))))[1]
try:
    a, used, _ = server.call_brain(cfg, [], capability="REASONING")
    check("a harder question goes to your stronger AI first", used == "claude-code" and asked == ["PREMIUM"])
    asked.clear()
    a, used, _ = server.call_brain(cfg, [], capability="GENERAL")
    check("an ordinary question stays on the free models", used == "free/general:free" and "PREMIUM" not in asked)
    asked.clear()
    a, used, _ = server.call_brain(cfg, [], capability="REASONING")
    check("the daily cap (2) is counted", premium.used_today(N) == 2)
    asked.clear()
    a, used, _ = server.call_brain(cfg, [], capability="REASONING")
    check("...and once it's reached, the free models answer", used == "free/general:free" and "PREMIUM" not in asked)
    shutil.rmtree(N); os.makedirs(N); models.refresh(N, fetch=lambda: LIST)
    def all_fail(api_key, model, messages):
        asked.append(model)
        raise urllib.error.HTTPError("u", 429, "busy", {}, None)
    server._call_openrouter_once = all_fail
    only_rescue = {"openrouter_api_key": "k", "premium": {"mode": "claude_code", "daily_cap": 5, "use_for": ["rescue"]}}
    asked.clear()
    a, used, _ = server.call_brain(only_rescue, [], capability="GENERAL")
    check("rescue: every free model busy -> your stronger AI answers once", a == "Premium answer, sir." and asked[-1] == "PREMIUM"
          and asked.count("PREMIUM") == 1 and len(asked) == server.MAX_ROUTER_TRIES + 1)
    premium.call = lambda config, messages, **k: (_ for _ in ()).throw(RuntimeError("Claude Code didn't answer"))
    server._call_openrouter_once = free
    asked.clear()
    a, used, _ = server.call_brain(cfg, [], capability="REASONING")
    check("if the stronger AI fails, a free model still answers", a == "Free answer, sir." and used != "claude-code")
    orc = {"openrouter_api_key": "k", "premium": {"mode": "openrouter", "model": "anthropic/some-paid-model", "use_for": ["reasoning"]}}
    asked.clear()
    a, used, _ = server.call_brain(orc, [], capability="REASONING")
    check("OpenRouter paid model: called through OpenRouter with that model", asked[0] == "anthropic/some-paid-model")
finally:
    for k, v in real.items():
        setattr(server, k, v)
    premium.call = real_call
    premium.claude_code_path = real_path

print("Test 5: Anthropic API key")
got = {}
class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
def fake_open(req, timeout):
    got.update(url=req.full_url, headers={k.lower(): v for k, v in req.header_items()}, body=json.loads(req.data))
    return Resp(json.dumps({"content": [{"type": "text", "text": "Certainly, sir."}]}).encode())
acfg = {"anthropic_api_key": "sk-ant-" + "y" * 30, "premium": {"mode": "anthropic_api"}}
ans, used = premium.call(acfg, [{"role": "system", "content": "BRIEF"}, {"role": "user", "content": "hi"}], urlopen=fake_open)
check("calls the Messages API with your key and a version header", got["url"].endswith("/v1/messages")
      and got["headers"]["x-api-key"].startswith("sk-ant-") and got["headers"]["anthropic-version"] == "2023-06-01")
check("the brief goes as the system prompt; the default model is used", got["body"]["system"] == "BRIEF"
      and got["body"]["messages"] == [{"role": "user", "content": "hi"}] and got["body"]["model"] == "claude-sonnet-5")
check("its reply is used", ans == "Certainly, sir." and used == "anthropic:claude-sonnet-5")
shutil.rmtree(N, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
