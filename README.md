# Odeifried

A real, bootable operating system (a Linux distribution based on Debian 13) that looks and works like
Windows and has a **local AI built in**. Press **Super+Space** (Windows key + Space) and tell it what to do.

100% free: the AI models run on your own machine through [Ollama](https://ollama.com). See `NOTICE` for credits.

## What you get
- A normal desktop: Start menu, taskbar, file manager, settings, browser, installer ("Install Odeifried").
- **Intent Bar** (Super+Space): type a task. `Tab` switches between **Do** (control the computer) and **Chat**.
- **Kill switch**: Super+Esc stops the AI instantly. `undo` reverses its file changes.
- Permission tiers: Safe runs, Ask shows the exact action first, Never is refused.

## How the AI stays fast
1. **Instant commands**: everyday requests are recognized without any AI model (well under a second).
   `organize my downloads` · `play lil baby on spotify` · `open youtube` · `volume 40` · `next song` ·
   `take a screenshot` · `dark mode` · `install spotify` · `search for ...` · `lock` · chain with `then`.
2. **Learned skills**: a task the AI solved once is replayed from memory next time.
3. **AI model**: everything else. A few seconds per step on a laptop CPU, much faster with a GPU.

## Models (edit `~/.config/odeifried/config.toml`)
| Setting | Job | Default |
|---|---|---|
| `agent` | does tasks; needs good tool calling | `qwen3:4b` |
| `chat` | the Chat tab; any Ollama model you like | same as agent |
| `vision` | reads screenshots (last resort, slow on CPU) | `qwen2.5vl:3b` |

## Get the ISO
- **Easiest:** push this repository to GitHub. The workflow in `.github/workflows/build-iso.yml` builds
  the ISO; download it from the run's *Artifacts*.
- **Build it yourself** on Debian 13 or Ubuntu 22.04+ (15 GB free disk):
  ```bash
  sudo os/build.sh
  ```
  Result: `dist/odeifried-<version>-amd64.iso`

## Try it in VirtualBox (Windows host)
1. Install Ollama on Windows and run `ollama pull qwen3:4b`.
2. New VM: type Linux / Debian 64-bit, 4 CPUs, 6 GB RAM, attach the ISO, start it.
3. Odeifried finds the Ollama on your Windows machine by itself. Press Super+Space.

Do not install it on a real computer yet: the installer has not been tested end to end.

## Project map
| Path | What it is |
|---|---|
| `odeifried/router.py` | instant commands and "do this, then that" splitting |
| `odeifried/agent.py` | picks the route (instant / skill / AI) and runs every action through the safety gate |
| `odeifried/safety/` | permission tiers, confirmation, kill switch |
| `odeifried/tools/` | files, shell, music, volume, power, app install |
| `odeifried/control/` | open apps, accessibility tree, vision fallback |
| `odeifried/memory.py` | SQLite: history, learned skills, cache |
| `odeifried/engine.py` | finds the Ollama server (this PC, or the VM host) |
| `odeifried/bar.py` | the Intent Bar window |
| `os/config/` | the OS recipe: packages, desktop layout, branding, installer |
| `os/build.sh` | builds the ISO |
| `os/test/vm.py` | boots an ISO in QEMU and takes screenshots |
| `tests/` | automated tests (safety first) |

## Develop
```bash
python3 -m pytest -q
```
