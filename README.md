# NEXUS — Setup Guide

Follow these steps exactly, in order. Each step takes 1–5 minutes.

---

## What you need
- A Mac (any model from the last 5 years)
- Google Chrome browser ([download here](https://www.google.com/chrome/) if you don't have it)
- An internet connection (for the download steps only — after setup it works offline)

---

## Step 1 — Install Ollama

Ollama is a free app that runs the AI on your Mac. It never sends your conversations anywhere.

1. Go to **[ollama.com](https://ollama.com)**
2. Click the **Download** button
3. Open the downloaded file and drag **Ollama** into your Applications folder
4. Open Ollama from your Applications folder — you'll see a small llama icon appear in your menu bar (top-right of your screen)

---

## Step 2 — Open Terminal

Terminal is a program that comes with every Mac. You'll use it to type a few commands.

1. Press **Command (⌘) + Space** to open Spotlight search
2. Type **Terminal** and press **Enter**
3. A window with a black or white background and a blinking cursor will open — that's Terminal

---

## Step 3 — Download the AI model

In Terminal, type the following and press **Enter**:

```
ollama pull llama3.2
```

You'll see a progress bar. This downloads a ~2 GB AI model to your Mac.
**Wait for it to finish** before moving on (it may take 5–10 minutes depending on your internet speed).

When it's done, you'll see something like: `success`

---

## Step 4 — Install the app's dependencies

Still in Terminal, type each of these lines and press **Enter** after each one:

```
pip3 install fastapi uvicorn httpx
```

Wait for it to finish. You'll see a message saying the installation succeeded.

> **If you see "command not found: pip3"**, type this instead and press Enter:
> ```
> python3 -m pip install fastapi uvicorn httpx
> ```

---

## Step 5 — Go to the NEXUS folder

You need to navigate Terminal to wherever you saved the NEXUS files.

**If the folder is in your Downloads:**
```
cd ~/Downloads/codex-exp
```

**If it's on your Desktop:**
```
cd ~/Desktop/codex-exp
```

Press **Enter**. You won't see much — that's normal.

---

## Step 6 — Start NEXUS

Type this and press **Enter**:

```
python3 server.py
```

You should see:

```
✅  NEXUS is running.
   Open this address in Chrome: http://localhost:8000
```

**Leave this Terminal window open** — closing it stops NEXUS.

---

## Step 7 — Open Chrome and go to NEXUS

1. Open **Google Chrome**
2. Click the address bar at the top
3. Type exactly: **http://localhost:8000**
4. Press **Enter**

You should see the NEXUS interface.

---

## Step 8 — Allow microphone access

When you click **Start recording** for the first time:

1. Chrome will show a pop-up asking to use your microphone
2. Click **Allow**

If you accidentally clicked "Block", fix it:
- Click the small lock icon to the left of the address bar
- Click **Site settings**
- Change **Microphone** to **Allow**
- Reload the page

---

## Using NEXUS

**To record:**
1. Select your language — **🇬🇷 Greek** or **🇬🇧 English** — using the buttons at the top
2. Click **▶ Start recording**
3. Speak — your words will appear on the left side in real time
4. Click **■ Stop recording** when done

**To ask NEXUS about what was said:**
- Type a question in the box on the right and press **Send**
- Examples:
  - *"Summarise what was discussed"*
  - *"What action items were mentioned?"*
  - *"List all the names that came up"*
  - *"Translate the conversation to English"*

**To start a new session:**
- Click **Clear** to wipe the transcript and start fresh

---

## Stopping NEXUS

Go back to the Terminal window and press **Control (^) + C**.

---

## Next time you want to run NEXUS

You don't need to repeat all the steps. Just:

1. Make sure Ollama is running (the llama icon is in your menu bar — if not, open Ollama from Applications)
2. Open Terminal
3. Navigate to the folder: `cd ~/Downloads/codex-exp`
4. Start the server: `python3 server.py`
5. Open Chrome and go to `http://localhost:8000`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach Ollama" error in the chat | Open the Ollama app from Applications, or run `ollama serve` in a new Terminal window |
| Microphone not working | Make sure you're using Chrome (not Safari). Allow microphone in Chrome settings. |
| Page won't load | Make sure `python3 server.py` is still running in Terminal |
| Speech recognition stops mid-sentence | This is normal — it restarts automatically. Pause briefly between thoughts. |
| "command not found: python3" | Install Python from [python.org](https://www.python.org/downloads/) |
