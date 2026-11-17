# Odeifried

An AI-native desktop for Linux: you state a goal, a **local** AI operates the computer to finish it.
100% free and offline: models run in [Ollama](https://ollama.com). Licensed Apache-2.0; see `NOTICE`.

## How it decides what to do (fastest + most reliable first)
1. **Direct tools**: files, shell, `web_search`, `open_app` (instant, no screen reading)
2. **Accessibility tree (AT-SPI)**: `list_ui` reads buttons/fields by name; `click_element` presses them
3. **Vision fallback**: `describe_and_click` screenshots, asks the vision model where to click, clicks with xdotool (slow on CPU, so last resort)

Every action passes the **safety policy** first: SAFE runs, ASK shows you the exact action, NEVER is refused.

## Project map
| File | What it does |
|---|---|
| `odeifried/default_config.toml` | Model names and paths. Swap models here. |
| `odeifried/config.py` | Loads defaults + your `~/.config/odeifried/config.toml` |
| `odeifried/hardware.py` | Detects RAM/GPU, recommends model sizes |
| `odeifried/llm.py` | Talks to Ollama (tool calling + images) |
| `odeifried/agent.py` | The loop: plan → safety check → act → verify → repeat |
| `odeifried/safety/policy.py` | **SAFE / ASK / NEVER rules** |
| `odeifried/safety/confirm.py` | The permission popup (zenity) or terminal prompt |
| `odeifried/safety/killswitch.py` | `odeifried stop` halts the agent immediately |
| `odeifried/journal.py` | Records file changes so `odeifried undo` can reverse them |
| `odeifried/audit.py` | Action log in `/var/log/odeifried/` |
| `odeifried/tools/files.py` | List/move/organize/trash files |
| `odeifried/tools/shell.py` | Run commands (non-root, 60 s timeout) |
| `odeifried/control/desktop.py` | Open apps/URLs, AT-SPI clicking, typing, screenshots |
| `odeifried/control/vision.py` | Screenshot shrinking + parsing click points from the vision model |
| `tests/` | 120 tests, mostly safety |

## Install (inside the Ubuntu VM, "Ubuntu on Xorg" session)
```bash
cd ~/odeifried
bash scripts/install.sh
```
Open a new terminal:
```bash
odeifried doctor
```
You should see `Ollama: reachable`, `planner qwen3:4b: installed`, `Desktop session: x11`, and `ok` for the tools.

On the **Windows host**, pull the vision model too (Phase 2): `ollama pull qwen2.5vl:3b`

## Phase 1 test
```bash
bash scripts/make_fake_downloads.sh
odeifried run "organize ~/Downloads by file type"
ls ~/Downloads
odeifried undo
```

## Phase 2 test
```bash
odeifried run "open Firefox, search for weather in New York, and screenshot the result"
ls ~/Pictures/Odeifried
```

## Other commands
`odeifried stop` (kill switch) · `odeifried undo` · `odeifried log` · `odeifried run --no-gui "..."`

## Run the tests
```bash
~/.local/share/odeifried/venv/bin/pytest -q
```
