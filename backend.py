# -*- coding: utf-8 -*-
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser  # to open link on browser
from collections import namedtuple
from typing import Tuple
from urllib import request

import requests
from diskcache import Cache

import services as s

cache = Cache(os.path.join(s.SETTINGS_DIR, 'cache'))

if sys.platform == "win32":
    import win32process
    import psutil
    import win32gui
elif sys.platform == "linux":
    import dbus
elif sys.platform == "darwin":
    import applescript


class Song:
    name = ""
    artist = ""
    album = "UNKNOWN"
    year = -1
    genre = "UNKNOWN"

    cycles_per_minute = -1
    beats_per_minute = -1
    dances = []

    def __init__(self, artist, name):
        self.artist = artist
        self.name = name
        self.dances = []

    @classmethod
    def get_from_string(cls, songstring: str):
        song_name_parts = songstring.split(" - ")
        artist = song_name_parts[0]
        if len(song_name_parts) > 2:
            name = " - ".join(song_name_parts[1:-1])
        else:
            name = song_name_parts[1]
        if " / " in name:
            name, garbage = name.rsplit(" / ", 1)
        name = re.sub(r' \(.*?\)', '', name, flags=re.DOTALL)
        name = re.sub(r' \[.*?\]', '', name, flags=re.DOTALL)
        return cls(artist, name)

    def __str__(self):
        return "%s: %s (%d) \nGenre: %s\nAlbum: %s\n" \
               "Cycles per minute: %d\nBeats per minute: %d\nDances: %s\n" \
               % (
                   self.artist, self.name, self.year, self.genre, self.album,
                   self.cycles_per_minute, self.beats_per_minute, self.dances,
               )


class StreamingService:
    def get_windows_executable_name(self) -> str:
        raise NotImplementedError

    def get_apple_script(self) -> str:
        raise NotImplementedError

    def get_linux_session_object_name(self) -> str:
        raise NotImplementedError

    def get_windows_exe_path(self) -> str:
        raise NotImplementedError

    def get_linux_open_command(self) -> str:
        raise NotImplementedError

    def get_apple_open_command(self) -> str:
        raise NotImplementedError

    def get_not_playing_windows_title(self) -> Tuple:
        raise NotImplementedError


class SpotifyStreamingService(StreamingService):
    def get_windows_executable_name(self) -> str:
        return 'Spotify.exe'

    def get_apple_script(self) -> str:
        return """set currentArtist to artist of current track as string
    set currentTrack to name of current track as string
    return currentArtist & " - " & currentTrack"""

    def get_linux_session_object_name(self) -> str:
        return "spotify"

    def get_windows_exe_path(self) -> str:
        return os.getenv("APPDATA") + '\\Spotify\\Spotify.exe'

    def get_linux_open_command(self) -> str:
        return "spotify"

    def get_apple_open_command(self) -> str:
        return "Spotify"

    def get_not_playing_windows_title(self) -> Tuple:
        return 'Spotify', 'Spotify Free', 'Spotify Premium', ''

    def __str__(self):
        return "Spotify"


class TidalStreamingService(StreamingService):
    def get_windows_executable_name(self) -> str:
        return 'TIDAL.exe'

    def get_apple_script(self) -> str:
        return ""  # TODO

    def get_linux_session_object_name(self) -> str:
        return ""  # Not supported on linux

    def get_windows_exe_path(self) -> str:
        return os.getenv("LOCALAPPDATA") + '\\TIDAL\\TIDAL.exe'

    def get_linux_open_command(self) -> str:
        return ""  # Not supported on linux

    def get_apple_open_command(self) -> str:
        return "Tidal"  # TODO

    def get_not_playing_windows_title(self) -> Tuple:
        return 'TIDAL', ''

    def __str__(self):
        return "Tidal"


class VlcMediaPlayer(StreamingService):
    def get_windows_executable_name(self) -> str:
        return 'vlc.exe'

    def get_apple_script(self) -> str:
        return "return get name of current item"

    def get_linux_session_object_name(self) -> str:
        return "vlc"

    def get_windows_exe_path(self) -> str:
        return os.getenv("PROGRAMFILES") + "\\VideoLAN\\VLC\\vlc.exe"

    def get_linux_open_command(self) -> str:
        return "vlc"

    def get_apple_open_command(self) -> str:
        return "VLC"  # TODO

    def get_not_playing_windows_title(self) -> Tuple:
        return 'VLC media player', ''

    def __str__(self):
        return "VLC"


# With Sync.
SERVICES_LIST1 = [s._local, s._minilyrics, s._qq, s._rentanadviser, s._syair, s._megalobiz, s._wikia]

# Without Sync.
SERVICES_LIST2 = [s._musixmatch, s._songmeanings, s._songlyrics, s._genius, s._versuri]

# Accords
SERVICES_LIST3 = [s._ultimateguitar, s._cifraclub, s._songsterr]

'''
current_service is used to store the current index of the list.
Useful to change the lyrics with the button "Next Lyric" if
the service returned a wrong song
'''
CURRENT_SERVICE = -1
SECONDS_IN_WEEK = 604800
LyricsMetadata = namedtuple("LyricsMetadata", ["lyrics", "url", "service_name", "timed"])


def cache_lyrics(func):
    def wrapper(*args, **kwargs):
        song = args[0]
        sync = kwargs.get("sync", False)
        ignore_cache = kwargs.get("ignore_cache", False)

        clean_song_name = '{}-{}'.format(song.artist, song.name)
        if not ignore_cache:
            lyrics_metadata = cache.get(clean_song_name)
            if lyrics_metadata is None:
                lyrics_metadata = func(*args, **kwargs)
                cache.set(clean_song_name, lyrics_metadata, expire=SECONDS_IN_WEEK)
            return lyrics_metadata
        else:
            lyrics_metadata = func(*args, **kwargs)
            cache.set(clean_song_name, lyrics_metadata, expire=SECONDS_IN_WEEK)
            return lyrics_metadata

    return wrapper


@cache_lyrics
def load_lyrics(song: Song, **kwargs):
    sync = kwargs.get("sync", False)
    global CURRENT_SERVICE

    if CURRENT_SERVICE == len(SERVICES_LIST2) - 1: CURRENT_SERVICE = -1

    if sync:
        temp_lyrics = []
        for service_synced in SERVICES_LIST1:
            lyrics, url, service_name, timed = service_synced(song)
            if lyrics != s.ERROR:
                if timed:
                    break
                else:
                    temp_lyrics = lyrics, url, service_name, timed
        if not timed and temp_lyrics and temp_lyrics[0] != s.ERROR:
            lyrics, url, service_name, timed = temp_lyrics
        CURRENT_SERVICE = -1

    if sync and lyrics == s.ERROR or sync is False:
        timed = False
        for i in range(CURRENT_SERVICE + 1, len(SERVICES_LIST2)):
            lyrics, url, service_name = SERVICES_LIST2[i](song)
            CURRENT_SERVICE = i
            if lyrics != s.ERROR:
                lyrics = lyrics.replace("&amp;", "&").replace("`", "'").strip()
                break
    if lyrics == s.ERROR:
        service_name = "---"

    # return "Error: Could not find lyrics."  if the for loop doesn't find any lyrics
    return LyricsMetadata(lyrics, url, service_name, timed)


def load_info(window, song: Song):
    def complete(function):
        function(song)
        window.refresh_info()

    threading.Thread(target=complete, args=(s._tanzmusikonline,)).start()
    threading.Thread(target=complete, args=(s._welchertanz,)).start()


def get_lyrics(song: Song, sync=False):
    global CURRENT_SERVICE
    CURRENT_SERVICE = -1

    return load_lyrics(song, sync=sync)


def next_lyrics(song: Song):
    return load_lyrics(song, ignore_cache=True)


def load_chords(song: Song):
    for i in SERVICES_LIST3:
        urls = i(song)
        for url in urls:
            webbrowser.open(url)


def get_window_title(service: StreamingService) -> str:
    window_name = ''
    if sys.platform == "win32":
        spotify_pids = []
        for proc in psutil.process_iter():
            if proc:
                try:
                    if proc.name() == service.get_windows_executable_name():
                        spotify_pids.append(proc.pid)
                except psutil.NoSuchProcess:
                    print("Process does not exist anymore")

        def enum_window_callback(hwnd, pid):
            tid, current_pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == current_pid and win32gui.IsWindowVisible(hwnd):
                windows.append(hwnd)

        windows = []
        try:
            for pid in spotify_pids:
                win32gui.EnumWindows(enum_window_callback, pid)
                for item in windows:
                    if win32gui.GetWindowText(item) != '':
                        window_name = win32gui.GetWindowText(item)
                        raise StopIteration
        except StopIteration:
            pass

    elif sys.platform == "darwin":
        try:
            r = applescript.tell.app(service.get_apple_open_command(), service.get_apple_script())
            window_name = r.out
        except Exception:
            pass
    else:
        try:
            session = dbus.SessionBus()
            spotify_dbus = session.get_object("org.mpris.MediaPlayer2.%s" % service.get_linux_session_object_name(),
                                              "/org/mpris/MediaPlayer2")
            spotify_interface = dbus.Interface(spotify_dbus, "org.freedesktop.DBus.Properties")
            metadata = spotify_interface.Get("org.mpris.MediaPlayer2.Player", "Metadata")
            window_name = "%s - %s" % (metadata['xesam:artist'][0], metadata['xesam:title'])
        except Exception:
            pass
        if not window_name:
            try:
                command = "xwininfo -tree -root"
                windows = subprocess.check_output(["/bin/bash", "-c", command]).decode("utf-8")
                for line in windows.splitlines():
                    if '("' + service.get_linux_open_command() + '" "' + service.get_linux_open_command() + '")' in line.lower():
                        if " - " in line:
                            window_name = line.split('"')[1]
                            break
            except Exception:
                pass
    if "—" in window_name:
        window_name = window_name.replace("—", "-")
    return window_name


def check_version() -> bool:
    proxy = request.getproxies()
    try:
        return get_version() >= \
               float(requests.get("https://api.github.com/repos/SimonIT/spotifylyrics/tags", timeout=5, proxies=proxy)
                     .json()[0]["name"])
    except Exception:
        return True


def get_version() -> float:
    return 1.4


def open_spotify(service: StreamingService):
    if sys.platform == "win32":
        if get_window_title(service) == "":
            subprocess.Popen(service.get_windows_exe_path())
        else:
            pass
    elif sys.platform == "linux":
        if get_window_title(service) == "":
            subprocess.Popen(service.get_linux_open_command())
        else:
            pass
    elif sys.platform == "darwin":
        if get_window_title(service) == "":
            subprocess.call(["open", "-a", service.get_apple_open_command()])
        else:
            pass
    else:
        pass


def main():
    if os.name == "nt":
        os.system("chcp 65001")

    def clear():
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")

    clear()
    old_song_name = ""
    service = SpotifyStreamingService()
    while True:
        song_name = get_window_title(service)
        if old_song_name != song_name:
            if song_name not in service.get_not_playing_windows_title():
                old_song_name = song_name
                clear()
        time.sleep(1)


if __name__ == '__main__':
    main()
