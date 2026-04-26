#!/bin/bash
# Sync Bass Speaker volume with Speaker (tweeter) volume
# for Lenovo Yoga Book 9 (83KJ) with Realtek ALC287
#
# Monitors "Speaker Playback Volume" (numid=13) and keeps
# "Bass Speaker Playback Volume" (numid=15) in sync.

SPEAKER_CTL="numid=13"
BASS_CTL="numid=15"
INTERVAL=0.5

last_vol=""

while true; do
    # Read current speaker volume
    vol=$(amixer cget "$SPEAKER_CTL" 2>/dev/null | grep -oP ': values=\K[0-9,]+')

    if [[ -n "$vol" && "$vol" != "$last_vol" ]]; then
        last_vol="$vol"
        amixer cset "$BASS_CTL" "$vol" >/dev/null 2>&1
    fi

    sleep "$INTERVAL"
done
