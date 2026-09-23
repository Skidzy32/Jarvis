# Jarvis — your notes as a galaxy you can talk to

Jarvis turns your notes into a 3D galaxy of stars and gives it a voice: a dry, very polite British butler that remembers things for you, files them, links related ideas, and answers questions from **your own notes**.

- Say *"remember that Mum's birthday is 3 May"* and a new star appears.
- Ask *"what am I putting off?"*, *"what did I decide about the van?"*, *"have I forgotten anything?"*
- Say *"link that with the budget note and file both under Money"*, or just right-click the stars.
- Short morning, evening and Sunday reviews; a list of everything you've said you'll do; focus sessions that notice when you drift off to YouTube (Windows).

It runs on **your own computer**. Your notes are ordinary text files in a folder called `notes`.

---

## Getting started (about 5 minutes)

### Windows
1. **[Download Jarvis-Setup.exe](https://github.com/Skidzy32/Jarvis/releases/latest/download/Jarvis-Setup.exe)** (or open **Releases** on the right of this page).
2. Run it. Windows may say *"Windows protected your PC"*, because Jarvis isn't code-signed: **More info → Run anyway**.
3. Click through the setup: you choose whether you want a Start menu entry and a Desktop icon, and which browser Jarvis opens in. If your computer doesn't have Python, Setup quietly installs a private copy just for Jarvis. No admin rights needed.

### Mac
1. Press the green **Code** button → **Download ZIP**, and unzip it.
2. Right-click **`Install Jarvis (Mac).command`** → **Open** → **Open** (macOS asks because it came from the internet). It asks before adding Jarvis to Applications and which browser to use, and offers to install Python if you don't have it.

*(Windows without Setup.exe: the ZIP also has **`Install Jarvis (Windows).bat`**, which does the same job in a console window.)*

### Then open Jarvis
From the Start menu (type *Jarvis*), your Desktop icon, or on a Mac from Launchpad/Spotlight. It opens in its own window with a boot screen. The first time, it walks you through four quick things:
1. **What it should call you:** "sir", "madam", your name, or nothing.
2. **Your free AI key.** Sign up at [openrouter.ai](https://openrouter.ai), go to [Keys](https://openrouter.ai/settings/keys), press *Create Key*, paste it in, press **Test**. Free, no card needed.
3. **Sample notes (optional):** a small made-up business, so the galaxy has something to show on day one. You can remove them later.
4. **A one-minute tour**, with a **Try it** button on each step.

After that: **? Help** (top-left) lists everything Jarvis can do with examples you can click. **⚙ Settings** changes your name, key, sample notes and browser, opens your notes folder, and has **Quit Jarvis** and **Uninstall…**.

### Updating, stopping, uninstalling
- **Update:** run the newer Jarvis-Setup.exe (Mac: the newer ZIP's installer). Your notes and settings are kept.
- **Stop:** ⚙ Settings → **Quit Jarvis** (on Windows, the × on the small Jarvis card does it too).
- **Uninstall:** ⚙ Settings → **Uninstall…**, or on Windows *Settings → Apps → Jarvis → Uninstall*. It asks whether to keep your notes (keeping them is the default) and removes Jarvis's private Python if Setup installed one. Mac: drag Jarvis from Applications to the Bin; your notes stay in `~/Library/Application Support/Jarvis` until you delete that folder.
- **Where your notes are:** ⚙ Settings → **Open my notes folder**. (Windows: `%LOCALAPPDATA%\Programs\Jarvis\notes`; Mac: `~/Library/Application Support/Jarvis/notes`.)

---

## What stays on your computer, and what doesn't

**Stays on your computer (always):**
- Your notes: plain text files in `notes/`. You can open them in any text editor.
- Jarvis's records of your notes (how they're filed and linked): `notes/.jarvis/`.
- Your key: `config.json` in the Jarvis folder. **Never send this file to anyone.**
- The webcam (if you use the 👁 posture feature): worked out entirely in your browser; no picture is sent anywhere unless you ask *"look at me"*.

**Sent over the internet:**
- When you ask Jarvis something, **your question and the few notes it needs** go to [OpenRouter](https://openrouter.ai), which passes them to a free AI model that writes the answer. The same happens when you ask it to sort your inbox or read a photo of a paper note.
- Free AI models may use what they're sent to improve themselves. Check [OpenRouter's privacy settings](https://openrouter.ai/settings/privacy) before sending anything very personal.

**Not sent anywhere:** there's no tracking, no analytics and no account with this project. Nobody, including whoever shared Jarvis with you, can see your notes.

**Nothing is lost by accident:** when you edit or rename a note from inside Jarvis, the old version is kept in `notes/.jarvis/versions/`. "Delete" moves a note to `notes/.jarvis/bin/`. Every change also comes with an **Undo** button.

---

## Windows and Mac

| Feature | Windows | Mac |
|---|---|---|
| Galaxy, chat, voice, remembering, sorting, linking, categories, reviews, overview | ✓ | ✓ |
| Webcam posture nudges, screen sharing ("what do you think of this?") | ✓ | ✓ (Chrome or Edge recommended) |
| Focus sessions (notices which app or site you switch to) | ✓ | not yet |
| Review reminders as pop-up notifications | ✓ | not yet; the reminder appears inside Jarvis instead |

Voice works best in **Chrome** or **Edge**.

---

## Good to know

- **The free AI:** Jarvis uses OpenRouter's free models, which change from time to time. The emblem on the right shows which model answered. The free allowance is about 50 requests a day, plenty for normal use. Topping up $10 once raises it to 1,000 a day.
- **Moving to another computer:** install Jarvis there, then copy your `notes` folder and `config.json` into its folder (⚙ Settings → Open my notes folder shows where).

## If something goes wrong

- **The Windows .bat installer says it can't find Python** even though you installed it: Settings → Apps → Advanced app settings → App execution aliases → turn **off** the ones for `python.exe` and `python3.exe`, then try again. (Jarvis-Setup.exe doesn't have this problem.)
- **Jarvis doesn't start:** the boot screen says so; the details are in `logs\server.log` in the Jarvis folder (Windows: `%LOCALAPPDATA%\Programs\Jarvis`). Opening Jarvis again from its icon usually sorts it.
- **Jarvis says the key isn't working:** ⚙ Settings → OpenRouter key → Change, paste a fresh key from openrouter.ai/keys, press Test.
- **The galaxy is empty:** that's normal until you save a note or add the sample notes (⚙ Settings).
- **Microphone doesn't work:** allow microphone access when the browser asks (the icon at the left of the address bar).

## For the curious

Plain Python (standard library only, no installs beyond Python itself) and a single web page, all in `app/`. `server.py` is the brain, `viewer/index.html` is the galaxy, and each feature has its own small file (`sorting.py`, `reviews.py`, `stars.py`, ...). `python preflight.py` (while Jarvis is running) checks everything end to end, and the `test_*.py` files are the tests.

Built by Liam, with Claude.
