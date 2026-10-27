#!/usr/bin/env python3
"""Bridge lokal Spotify (MPRIS) + LRCLIB untuk Audio Vibe.

Kenapa butuh server kecil ini?
  Browser tidak bisa membaca D-Bus/MPRIS secara langsung. Server ini:
    - membaca lagu & posisi playback Spotify dari MPRIS,
    - mengambil lirik dari LRCLIB / lyrics.ovh / folder .lrc lokal,
    - menerjemahkan baris lirik (opsional) lewat Google Translate,
    - menyajikan halaman Audio Vibe + API JSON ke browser.

Jalankan:
    python3 server.py --open
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
except Exception as exc:  # pragma: no cover - pesan ramah untuk pengguna
    raise SystemExit(
        "Butuh PyGObject (python3-gi) untuk membaca Spotify via MPRIS.\n"
        "Di Ubuntu: sudo apt install python3-gi\n"
        f"Detail: {exc}"
    ) from exc


# --------------------------------------------------------------------------
# Konstanta & path
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
CACHE_DIR = Path.home() / ".cache" / "audio-visual"
LYRICS_CACHE_DIR = CACHE_DIR / "lyrics"
WIDGET_LYRICS_DIR = Path.home() / ".local" / "share" / "spotify-lyrics-widget" / "lyrics"
LOCAL_LYRICS_DIR = Path.home() / ".local" / "share" / "audio-visual" / "lyrics"

SPOTIFY_BUS = "org.mpris.MediaPlayer2.spotify"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

LRCLIB = "https://lrclib.net/api"
LYRICS_OVH = "https://api.lyrics.ovh/v1"
GOOGLE_TRANSLATE = "https://translate.googleapis.com/translate_a/single"

USER_AGENT = "AudioVibe/1.0 (local desktop app)"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

CACHE_VERSION = 3
TARGET_LANGUAGES = (("en", "English"), ("id", "Indonesia"))
TRANSLATE_BUDGET_SECONDS = 6.0
LYRICS_BUDGET_SECONDS = 18.0


# --------------------------------------------------------------------------
# Util teks
# --------------------------------------------------------------------------


def strip_noise(text: str) -> str:
    """Buang embel-embel judul: (Official Audio), - Remastered, feat. ..., dll."""
    if not text:
        return ""
    out = text
    out = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*", " ", out)
    out = re.sub(
        r"\s*\b(feat\.?|ft\.?|featuring|with|vs\.?|and)\b\s*[^,]*$",
        " ",
        out,
        flags=re.IGNORECASE,
    )
    out = re.split(r"\s+[-–—]\s+", out)[0]
    return re.sub(r"\s+", " ", out).strip()


def normalize(text: str) -> str:
    """Normalisasi nama file/lagu, tetap mendukung huruf non-Latin."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*", " ", t)
    t = re.sub(
        r"\b(feat|ft|featuring|with|prod|original|mix|remaster(ed)?|"
        r"explicit|audio|video|official|lyric(s)?|hq|4k)\b",
        " ",
        t,
    )
    t = re.sub(r"[^\w]+", " ", t, flags=re.UNICODE)
    return t.strip()


def build_candidates(title: str, artist: str) -> tuple[list[str], list[str]]:
    raw_title = (title or "").strip()
    raw_artist = (artist or "").strip()

    titles: list[str] = []
    for value in (raw_title, strip_noise(raw_title)):
        value = (value or "").strip()
        if value and value not in titles:
            titles.append(value)

    artists: list[str] = []
    for value in (raw_artist, strip_noise(raw_artist)):
        value = (value or "").strip()
        if value and value not in artists:
            artists.append(value)

    for part in re.split(r"\s*(?:,|&|\bx\b|\+)\s*", raw_artist):
        part = strip_noise(part)
        if len(part) > 1 and part not in artists:
            artists.append(part)

    return titles, artists


def parse_lrc(lrc: str) -> list[dict]:
    """Parse LRC → [{time: ms, text: str}] (diurutkan)."""
    lines: list[dict] = []
    if not lrc:
        return lines

    offset = 0
    for raw in lrc.splitlines():
        offset_match = re.match(r"^\[offset:\s*([+-]?\d+)\s*\]", raw, re.IGNORECASE)
        if offset_match:
            offset = int(offset_match.group(1))
            continue

        stamps: list[int] = []
        consumed = 0
        stamp_re = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
        for match in stamp_re.finditer(raw):
            if match.start() != consumed:
                break
            consumed = match.end()
            frac = match.group(3) or "0"
            frac_ms = int((frac + "000")[:3])
            stamps.append(
                int(match.group(1)) * 60_000
                + int(match.group(2)) * 1_000
                + frac_ms
            )

        if not stamps:
            continue

        text = raw[consumed:].strip()
        for stamp in stamps:
            lines.append({"time": max(0, stamp + offset), "text": text})

    lines.sort(key=lambda item: item["time"])
    return lines


def plain_to_lines(plain: str, duration: float) -> list[dict]:
    """Lirik tanpa timestamp → dibagi rata sepanjang durasi."""
    texts = [
        line.strip()
        for line in (plain or "").splitlines()
        if line.strip() and not re.match(r"^\[.*\]$", line.strip())
    ]
    if not texts:
        return []

    total_ms = duration * 1000 if duration and duration > 10 else 240_000
    step = total_ms / (len(texts) + 1)
    return [
        {"time": int(step * (index + 1)), "text": text}
        for index, text in enumerate(texts)
    ]


def same_lines(left: list[str], right: list[str]) -> bool:
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if re.sub(r"\s+", " ", a).strip().casefold() != re.sub(r"\s+", " ", b).strip().casefold():
            return False
    return True


# --------------------------------------------------------------------------
# HTTP helper
# --------------------------------------------------------------------------


def http_json(url: str, timeout: float = 10.0, headers: dict | None = None):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Spotify / MPRIS
# --------------------------------------------------------------------------


# Error ini berarti Spotify-nya yang tidak ada, bukan bus-nya yang rusak.
PLAYER_ABSENT_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.UnknownObject",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownProperty",
    }
)


class SpotifyPlayer:
    """Baca metadata/posisi dari MPRIS dan interpolasi posisi biar mulus."""

    def __init__(self) -> None:
        self._bus = None
        self._lock = threading.Lock()
        self._last_position = 0.0
        self._last_at = time.monotonic()
        self._last_status = "Stopped"
        self._last_track = None

    # -- internal ---------------------------------------------------------

    def _connect_bus(self) -> Gio.DBusConnection:
        """
        Koneksi privat, bukan Gio.bus_get_sync(), supaya tidak ikut cache
        global dan bisa dibuang-sambung ulang sendiri.
        """
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        bus = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        # Jangan matikan proses kalau bus-nya hilang; kita sambung ulang saja.
        bus.set_exit_on_close(False)
        return bus

    def _ensure_bus(self) -> None:
        """
        Logout/login bikin dbus-daemon baru, sedangkan koneksi lama tetap
        "hidup" tapi bicara ke daemon yang sudah mati — gejalanya Spotify
        selamanya terbaca tidak terdeteksi.
        """
        if self._bus is not None:
            return
        self._bus = self._connect_bus()

    def _note_failure(self, exc: BaseException) -> None:
        """
        Spotify mati itu error yang wajar dan bus-nya tetap sehat, jadi
        koneksinya dipertahankan. Error lain berarti bus-nya yang bermasalah:
        buang koneksinya supaya panggilan berikutnya menyambung ulang.

        Tidak memakai is_closed() karena GDBus baru menandai koneksi tertutup
        belakangan — waktu dbus-daemon diganti, is_closed() masih False.
        """
        remote = ""
        if isinstance(exc, GLib.Error):
            remote = Gio.DBusError.get_remote_error(exc) or ""
        if remote in PLAYER_ABSENT_ERRORS:
            return
        self._bus = None

    def _get_all(self) -> dict:
        self._ensure_bus()
        try:
            reply = self._bus.call_sync(
                SPOTIFY_BUS,
                MPRIS_PATH,
                PROPERTIES_IFACE,
                "GetAll",
                GLib.Variant("(s)", [PLAYER_IFACE]),
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
        except Exception as exc:
            self._note_failure(exc)
            raise
        return reply.unpack()[0]

    def _call(self, method: str, params=None) -> None:
        self._ensure_bus()
        try:
            self._bus.call_sync(
                SPOTIFY_BUS,
                MPRIS_PATH,
                PLAYER_IFACE,
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                2500,
                None,
            )
        except Exception as exc:
            self._note_failure(exc)
            raise

    def _reset(self) -> None:
        self._last_position = 0.0
        self._last_at = time.monotonic()
        self._last_status = "Stopped"
        self._last_track = None

    # -- public -----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            try:
                props = self._get_all()
            except Exception:
                self._reset()
                return {"connected": False}

            metadata = props.get("Metadata") or {}
            status = props.get("PlaybackStatus") or "Stopped"
            position_us = props.get("Position") or 0
            duration_us = metadata.get("mpris:length") or 0
            track_id = metadata.get("mpris:trackid") or ""

            try:
                real_position = float(position_us) / 1_000_000.0
            except (TypeError, ValueError):
                real_position = 0.0

            artists = metadata.get("xesam:artist") or []
            if isinstance(artists, str):
                artists = [artists]
            artists = [str(item) for item in artists if item]

            now = time.monotonic()
            track_key = str(track_id) or f"{metadata.get('xesam:title', '')}|{', '.join(artists)}"
            force = track_key != self._last_track or status != self._last_status
            predicted = self._last_position
            if self._last_status == "Playing":
                predicted += now - self._last_at

            if force or abs(real_position - predicted) > 1.5:
                self._last_position = real_position
            else:
                self._last_position = predicted

            self._last_at = now
            self._last_status = status
            self._last_track = track_key

            return {
                "connected": True,
                "playing": status == "Playing",
                "status": status,
                "title": str(metadata.get("xesam:title") or ""),
                "artist": ", ".join(artists),
                "artists": artists,
                "album": str(metadata.get("xesam:album") or ""),
                "artUrl": str(metadata.get("mpris:artUrl") or ""),
                "duration": max(0.0, float(duration_us) / 1_000_000.0),
                "position": max(0.0, round(self._last_position, 3)),
                "trackId": str(track_id),
                "serverTime": int(time.time() * 1000),
            }

    def play_pause(self) -> None:
        with self._lock:
            self._call("PlayPause")

    def next(self) -> None:
        with self._lock:
            self._call("Next")

    def previous(self) -> None:
        with self._lock:
            self._call("Previous")

    def seek_to(self, seconds: float) -> None:
        current = self.snapshot()
        if not current.get("connected"):
            raise RuntimeError("Spotify tidak terdeteksi")

        target = max(0.0, min(float(seconds), current.get("duration") or float(seconds)))
        delta_us = int(round((target - current.get("position", 0.0)) * 1_000_000))
        with self._lock:
            self._call("Seek", GLib.Variant("(x)", [delta_us]))
            self._last_position = target
            self._last_at = time.monotonic()


# --------------------------------------------------------------------------
# Lirik: lokal → LRCLIB → lyrics.ovh
# --------------------------------------------------------------------------


class LyricsService:
    def __init__(self) -> None:
        LYRICS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_LYRICS_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def get(self, track: dict, refresh: bool = False) -> dict:
        key = self._cache_key(track)
        cache_file = LYRICS_CACHE_DIR / f"{key}.json"

        if not refresh and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                is_empty = not cached.get("lines")
                expired = time.time() - float(cached.get("ts", 0)) > 600
                if not (is_empty and expired):
                    cached["cached"] = True
                    return cached
            except (OSError, ValueError):
                pass

        with self._lock:
            # Cek lagi: request lain mungkin sudah selesai saat kita menunggu.
            if not refresh and cache_file.exists():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    is_empty = not cached.get("lines")
                    expired = time.time() - float(cached.get("ts", 0)) > 600
                    if not (is_empty and expired):
                        cached["cached"] = True
                        return cached
                except (OSError, ValueError):
                    pass

            result = self._build(track)
            result["ts"] = time.time()
            result["cached"] = False
            try:
                tmp = cache_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                tmp.replace(cache_file)
            except OSError:
                pass
            return result

    def _cache_key(self, track: dict) -> str:
        raw = "|".join(
            [
                str(CACHE_VERSION),
                track.get("title", ""),
                track.get("artist", ""),
                track.get("album", ""),
                str(round(float(track.get("duration", 0) or 0))),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _build(self, track: dict) -> dict:
        deadline = time.monotonic() + LYRICS_BUDGET_SECONDS

        local = self._local(track)
        if local:
            return self._finish(local, track)

        result = self._lrclib(track, deadline)
        if not result:
            result = self._ovh(track)
        if not result:
            return {
                "lines": [],
                "translations": {},
                "source": None,
                "synced": False,
                "error": "Lirik tidak ditemukan",
            }

        return self._finish(result, track)

    def _finish(self, result: dict, track: dict) -> dict:
        lines = result["lines"]
        texts = [line["text"] for line in lines]
        translations: dict[str, list[str]] = {}

        if texts:
            deadline = time.monotonic() + TRANSLATE_BUDGET_SECONDS
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    code: pool.submit(self._translate, texts, code, deadline)
                    for code, _label in TARGET_LANGUAGES
                }
                for code, future in futures.items():
                    try:
                        translated = future.result(timeout=TRANSLATE_BUDGET_SECONDS + 1)
                    except Exception:
                        translated = None
                    if translated and len(translated) == len(texts):
                        translations[code] = translated

        return {
            "lines": lines,
            "translations": translations,
            "source": result["source"],
            "synced": bool(result.get("synced")),
            "error": None,
        }

    # -- sumber lokal -----------------------------------------------------

    def _local(self, track: dict) -> dict | None:
        want_title = normalize(track.get("title", ""))
        want_artist = normalize(track.get("artist", ""))
        if not want_title:
            return None

        wanted = {
            want_title,
            normalize(f"{track.get('artist', '')} {track.get('title', '')}"),
            f"{want_artist} {want_title}".strip(),
        }

        for directory in (LOCAL_LYRICS_DIR, WIDGET_LYRICS_DIR):
            if not directory.exists():
                continue
            try:
                files = list(directory.iterdir())
            except OSError:
                continue

            for file in files:
                if file.suffix.lower() not in {".lrc", ".txt"} or not file.is_file():
                    continue
                if normalize(file.stem) not in wanted:
                    continue
                try:
                    content = file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                lines = parse_lrc(content)
                if lines:
                    return {"lines": lines, "source": f"Lokal · {file.name}", "synced": True}

                plain = plain_to_lines(content, float(track.get("duration", 0) or 0))
                if plain:
                    return {
                        "lines": plain,
                        "source": f"Lokal · {file.name}",
                        "synced": False,
                    }
        return None

    # -- LRCLIB -----------------------------------------------------------

    def _lrclib(self, track: dict, deadline: float) -> dict | None:
        title = track.get("title", "")
        artist = track.get("artist", "")
        album = track.get("album", "")
        duration = float(track.get("duration", 0) or 0)
        titles, artists = build_candidates(title, artist)

        seen: set[tuple[str, str]] = set()
        combos = []
        for candidate_title in titles:
            for candidate_artist in artists:
                key = (candidate_title, candidate_artist)
                if key in seen:
                    continue
                seen.add(key)
                combos.append(key)

        # 1) Exact match judul/artis asli (paling akurat, biasanya 1 request).
        if combos and time.monotonic() < deadline:
            result = self._lrclib_get(combos[0][0], combos[0][1], album, duration)
            if result:
                return result

        # 2) Pencarian bebas "judul artis" (sering langsung ketemu).
        free = f"{title} {artist}".strip()
        if free and time.monotonic() < deadline:
            result = self._lrclib_search_free(free, duration)
            if result:
                return result

        # 3) Variasi judul/artis yang sudah dibersihkan (tanpa feat/remaster/dll).
        for candidate_title, candidate_artist in combos[1:3]:
            if time.monotonic() >= deadline:
                break
            result = self._lrclib_get(candidate_title, candidate_artist, album, duration)
            if result:
                return result
            result = self._lrclib_search(candidate_title, candidate_artist, duration)
            if result:
                return result

        return None

    def _lrclib_get(
        self, title: str, artist: str, album: str, duration: float
    ) -> dict | None:
        params = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration > 0:
            params["duration"] = str(round(duration))

        try:
            data = http_json(f"{LRCLIB}/get?{urllib.parse.urlencode(params)}", timeout=8)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return self._from_lrclib_item(data, duration)

    def _lrclib_search(self, title: str, artist: str, duration: float) -> dict | None:
        params = {"track_name": title, "artist_name": artist}
        try:
            data = http_json(f"{LRCLIB}/search?{urllib.parse.urlencode(params)}", timeout=8)
        except Exception:
            return None
        return self._pick_search(data, duration)

    def _lrclib_search_free(self, query: str, duration: float) -> dict | None:
        params = {"q": query}
        try:
            data = http_json(f"{LRCLIB}/search?{urllib.parse.urlencode(params)}", timeout=8)
        except Exception:
            return None
        return self._pick_search(data, duration)

    def _from_lrclib_item(self, item: dict, duration: float) -> dict | None:
        synced = parse_lrc(item.get("syncedLyrics") or "")
        if synced:
            return {"lines": synced, "source": "LRCLIB", "synced": True}

        plain_text = item.get("plainLyrics") or ""
        if plain_text:
            plain = plain_to_lines(plain_text, duration)
            if plain:
                return {"lines": plain, "source": "LRCLIB (plain)", "synced": False}
        return None

    def _pick_search(self, data, duration: float) -> dict | None:
        if not isinstance(data, list) or not data:
            return None

        synced = [item for item in data if item.get("syncedLyrics")]
        pool = synced if synced else [item for item in data if item.get("plainLyrics")]
        if not pool:
            return None

        best = None
        best_diff = float("inf")
        for item in pool:
            item_duration = float(item.get("duration") or 0)
            diff = abs(item_duration - duration) if duration > 0 else 0
            if diff < best_diff:
                best_diff = diff
                best = item

        if best is None:
            return None
        if duration > 0 and best_diff > 12:
            return None
        return self._from_lrclib_item(best, duration)

    # -- lyrics.ovh -------------------------------------------------------

    def _ovh(self, track: dict) -> dict | None:
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        if not title or not artist:
            return None

        url = f"{LYRICS_OVH}/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
        try:
            data = http_json(url, timeout=8)
        except Exception:
            return None

        lyrics = data.get("lyrics") if isinstance(data, dict) else None
        if not lyrics:
            return None

        lines = plain_to_lines(lyrics, float(track.get("duration", 0) or 0))
        if not lines:
            return None
        return {"lines": lines, "source": "lyrics.ovh", "synced": False}

    # -- terjemahan -------------------------------------------------------

    def _translate(self, lines: list[str], target: str, deadline: float) -> list[str] | None:
        if time.monotonic() > deadline:
            return None

        # Bagi per chunk (~1200 karakter) supaya URL Google tidak kepanjangan,
        # tapi tetap sedikit request. Baris tetap sejajar karena chunk-nya
        # berisi baris utuh.
        chunks: list[tuple[int, list[str]]] = []
        current: list[str] = []
        current_length = 0
        start = 0

        for index, line in enumerate(lines):
            add = len(line) + 1
            if current and current_length + add > 1200:
                chunks.append((start, current))
                start = index
                current = []
                current_length = 0
            current.append(line)
            current_length += add
        if current:
            chunks.append((start, current))

        results: list[str] = list(lines)
        changed = False

        for chunk_start, chunk in chunks:
            if time.monotonic() > deadline:
                break

            translated = self._google_batch(chunk, target)
            if not translated or len(translated) != len(chunk):
                translated = self._google_per_line(chunk, target, deadline)

            if translated and len(translated) == len(chunk):
                for offset, value in enumerate(translated):
                    results[chunk_start + offset] = value
                    if value.strip() != chunk[offset].strip():
                        changed = True

        return results if changed else None

    def _google_batch(self, lines: list[str], target: str) -> list[str] | None:
        query = urllib.parse.urlencode(
            {
                "client": "dict-chrome-ex",
                "sl": "auto",
                "tl": target,
                "dt": "t",
                "q": "\n".join(lines),
            }
        )
        try:
            data = http_json(
                f"{GOOGLE_TRANSLATE}?{query}",
                timeout=6,
                headers={"User-Agent": BROWSER_UA},
            )
        except Exception:
            return None

        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            return None

        text = "".join(segment[0] for segment in data[0] if segment and segment[0])
        parts = text.split("\n")
        if len(parts) != len(lines):
            return None
        return [part.strip() for part in parts]

    def _google_per_line(
        self, lines: list[str], target: str, deadline: float
    ) -> list[str] | None:
        results: list[str] = list(lines)
        indexes = [index for index, line in enumerate(lines) if line.strip()]

        def translate_one(index: int) -> None:
            if time.monotonic() > deadline:
                return
            query = urllib.parse.urlencode(
                {
                    "client": "dict-chrome-ex",
                    "sl": "auto",
                    "tl": target,
                    "dt": "t",
                    "q": lines[index],
                }
            )
            try:
                data = http_json(
                    f"{GOOGLE_TRANSLATE}?{query}",
                    timeout=5,
                    headers={"User-Agent": BROWSER_UA},
                )
                if isinstance(data, list) and data and isinstance(data[0], list):
                    translated = "".join(
                        segment[0] for segment in data[0] if segment and segment[0]
                    ).strip()
                    if translated:
                        results[index] = translated
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(translate_one, indexes))

        return results if results != lines else None


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, player: SpotifyPlayer, lyrics: LyricsService):
        super().__init__(address, handler)
        self.player = player
        self.lyrics = lyrics


class Handler(BaseHTTPRequestHandler):
    server_version = "AudioVibe/1.0"

    # -- util -------------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: D401 - terlalu berisik untuk polling
        return

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/now-playing":
            self._send_json(self.server.player.snapshot())
            return

        if path == "/api/lyrics":
            track = {
                "title": query.get("title", [""])[0],
                "artist": query.get("artist", [""])[0],
                "album": query.get("album", [""])[0],
                "duration": query.get("duration", ["0"])[0],
            }
            if not track["title"]:
                self._send_error_json("Parameter title wajib diisi", 400)
                return
            refresh = query.get("refresh", ["0"])[0] in {"1", "true", "yes"}
            try:
                result = self.server.lyrics.get(track, refresh=refresh)
            except Exception as exc:
                self._send_error_json(f"Gagal mengambil lirik: {exc}", 502)
                return
            self._send_json(result)
            return

        if path == "/api/art":
            self._serve_art(query.get("url", [""])[0])
            return

        if path == "/api/health":
            snapshot = self.server.player.snapshot()
            self._send_json({"ok": True, "spotify": bool(snapshot.get("connected"))})
            return

        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/control":
            self._send_error_json("Tidak ditemukan", 404)
            return

        payload = self._read_json()
        action = str(payload.get("action") or "")
        try:
            if action == "play_pause":
                self.server.player.play_pause()
            elif action == "next":
                self.server.player.next()
            elif action == "previous":
                self.server.player.previous()
            elif action == "seek":
                self.server.player.seek_to(float(payload.get("position", 0)))
            else:
                self._send_error_json("Aksi tidak dikenal", 400)
                return
        except Exception as exc:
            self._send_error_json(f"Kontrol Spotify gagal: {exc}", 502)
            return

        self._send_json({"ok": True, "now": self.server.player.snapshot()})

    # -- static & art -----------------------------------------------------

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"

        allowed = {"/index.html", "/romaji.js"}
        if path not in allowed:
            self._send_error_json("Tidak ditemukan", 404)
            return

        file_path = (ROOT / path.lstrip("/")).resolve()
        if not file_path.is_file() or file_path.parent != ROOT:
            self._send_error_json("Tidak ditemukan", 404)
            return

        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if file_path.name == "index.html":
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _serve_art(self, url: str) -> None:
        if not url:
            self._send_error_json("Parameter url kosong", 400)
            return

        parsed = urllib.parse.urlparse(url)
        data = None
        content_type = "image/jpeg"

        try:
            if parsed.scheme == "file":
                file_path = Path(urllib.request.url2pathname(parsed.path)).resolve()
                if not file_path.is_file() or file_path.stat().st_size > 8_000_000:
                    raise ValueError("File gambar tidak valid")
                data = file_path.read_bytes()
                content_type = mimetypes.guess_type(file_path.name)[0] or content_type
            elif parsed.scheme in {"http", "https"}:
                host = (parsed.hostname or "").lower()
                allowed_hosts = (
                    "scdn.co",
                    "spotifycdn.com",
                    "spotify.com",
                )
                if not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
                    raise ValueError("Host gambar tidak diizinkan")
                request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
                with urllib.request.urlopen(request, timeout=8) as response:
                    data = response.read(8_000_001)
                    content_type = response.headers.get("Content-Type", content_type)
                if len(data) > 8_000_000:
                    raise ValueError("Gambar terlalu besar")
            else:
                raise ValueError("Skema URL tidak didukung")
        except Exception:
            self._send_error_json("Gagal memuat cover", 404)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Audio Vibe — Spotify bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Buka browser otomatis")
    args = parser.parse_args()

    player = SpotifyPlayer()
    lyrics = LyricsService()
    try:
        server = AppServer((args.host, args.port), Handler, player, lyrics)
    except OSError as exc:
        raise SystemExit(
            f"Tidak bisa membuka {args.host}:{args.port} — {exc}\n"
            f"Kalau server sudah jalan, buka http://{args.host}:{args.port}/ "
            "atau jalankan dengan --port lain."
        ) from exc
    url = f"http://{args.host}:{args.port}/"

    print("Audio Vibe — Spotify x LRCLIB")
    print(f"  Buka: {url}")
    snapshot = player.snapshot()
    if snapshot.get("connected"):
        title = snapshot.get("title") or "(tanpa judul)"
        artist = snapshot.get("artist") or ""
        state = "playing" if snapshot.get("playing") else snapshot.get("status", "paused")
        print(f"  Spotify: {title} — {artist} ({state})")
    else:
        print("  Spotify: belum terdeteksi (buka Spotify lalu putar lagu)")
    print("  Ctrl+C untuk berhenti.\n")

    if args.open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
