"""Play music by name. Spotify is controlled through the standard Linux media interface (MPRIS),
not by clicking around its window, so it is fast and does not break when Spotify changes its look."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from . import Tool, have, launch_detached, run

MPRIS = "org.mpris.MediaPlayer2.spotify"
_UA = "Odeifried/0.2 (desktop assistant)"


# ---------- finding the artist on Spotify (no account or API key needed) ----------
def _get_json(url: str, timeout: float = 4):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pick_artist(search: dict, query: str) -> str | None:
    """Best MusicBrainz artist id for the query, or None if nothing matches confidently."""
    want = re.sub(r"[^a-z0-9]", "", query.lower())
    for a in search.get("artists", []):
        name = re.sub(r"[^a-z0-9]", "", a.get("name", "").lower())
        if int(a.get("score", 0)) >= 90 and name == want:
            return a.get("id")
    return None


def spotify_id_from_relations(artist: dict) -> str | None:
    for rel in artist.get("relations", []):
        m = re.match(r"https?://open\.spotify\.com/artist/([A-Za-z0-9]+)", rel.get("url", {}).get("resource", ""))
        if m:
            return m.group(1)
    return None


def resolve_spotify_uri(ctx, query: str) -> str | None:
    """'lil baby' -> 'spotify:artist:...' using the open MusicBrainz database. Cached forever."""
    key = "spotify-artist:" + query.lower().strip()
    mem = getattr(ctx, "memory", None)
    if mem and (hit := mem.cache_get(key)):
        return hit or None
    try:
        q = urllib.parse.quote(f'artist:"{query}"')
        mbid = pick_artist(_get_json(f"https://musicbrainz.org/ws/2/artist?query={q}&fmt=json&limit=5"), query)
        if not mbid:
            return None
        sid = spotify_id_from_relations(_get_json(f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels&fmt=json"))
    except Exception:  # noqa: BLE001 - offline or service down: caller falls back to a search
        return None
    uri = f"spotify:artist:{sid}" if sid else None
    if mem and uri:
        mem.cache_set(key, uri)
    return uri


# ---------- talking to the Spotify app ----------
def _gdbus(*args: str) -> tuple[int, str]:
    return run(["gdbus", "call", "--session", *args], timeout=8)


def spotify_running() -> bool:
    rc, out = _gdbus("--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                     "--method", "org.freedesktop.DBus.NameHasOwner", MPRIS)
    return rc == 0 and "true" in out


def _player(method: str, *params: str) -> tuple[int, str]:
    return _gdbus("--dest", MPRIS, "--object-path", "/org/mpris/MediaPlayer2",
                  "--method", f"org.mpris.MediaPlayer2.Player.{method}", *params)


def now_playing() -> tuple[str, str]:
    """(status, 'Title by Artist')."""
    _, status = _gdbus("--dest", MPRIS, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                       "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
    _, meta = _gdbus("--dest", MPRIS, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                     "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", "Metadata")
    title = re.search(r"'xesam:title': <'((?:[^'\\]|\\.)*)'>", meta)
    artist = re.search(r"'xesam:artist': <\['((?:[^'\\]|\\.)*)'", meta)
    what = f"{title.group(1)} by {artist.group(1)}" if title and artist else (title.group(1) if title else "")
    state = "Playing" if "Playing" in status else "Paused" if "Paused" in status else "Stopped"
    return state, what


def _spotify(ctx, query: str) -> str:
    from ..control.desktop import find_desktop_entry, open_url
    web = "https://open.spotify.com/search/" + urllib.parse.quote(query)
    entry = find_desktop_entry("spotify")
    if not entry and not spotify_running():
        open_url(ctx, web)
        return (f"The Spotify app isn't installed, so I opened Spotify in the browser on a search for "
                f"'{query}'. Log in and press play. (Say \"install spotify\" to get the app, which I can control fully.)")
    if not spotify_running():
        launch_detached(["gtk-launch", entry])
        for _ in range(40):                                   # Spotify takes a few seconds to appear
            if spotify_running():
                time.sleep(2)
                break
            time.sleep(0.5)
        else:
            return "ERROR: Spotify did not start. Open it once and log in, then ask again."
    uri = resolve_spotify_uri(ctx, query) or "spotify:search:" + urllib.parse.quote(query)
    rc, out = _player("OpenUri", uri)
    if rc != 0:
        return f"ERROR: Spotify refused the request ({out[:120]})"
    if uri.startswith("spotify:search:"):
        return f"Opened Spotify on a search for '{query}' (I couldn't pin down the exact artist). Press play on the one you want."
    for _ in range(10):                                       # verify that sound is actually playing
        time.sleep(0.6)
        state, what = now_playing()
        if state == "Playing":
            return f"VERIFIED Spotify is playing {what or query}"
    _player("Play")
    return f"Opened {query} in Spotify. If nothing plays, log in to Spotify once and ask again."


def _youtube(ctx, query: str) -> str:
    from ..control.desktop import open_url
    open_url(ctx, "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
    return f"Opened YouTube results for '{query}'. Click the video you want."


def play_music(ctx, query: str, service: str = "") -> str:
    service = (service or ctx.cfg.get("music", "service", "spotify")).lower()
    if not have("gdbus") and service == "spotify":
        return "ERROR: this desktop is missing the 'gdbus' tool needed to control Spotify"
    return _youtube(ctx, query) if service in {"youtube", "yt"} else _spotify(ctx, query)


_S = {"type": "string"}
TOOLS = [Tool("play_music", "Play an artist or song. service: spotify (default) or youtube.",
              {"query": _S, "service": _S}, ["query"], play_music)]
