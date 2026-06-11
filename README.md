# red-audio-radio

Red V3 cog for inserting configurable ads and jingles around Red Audio playback.

Package path: `red_audio_radio/`

Load it in Red with:

1. `[p]addpath <this-repo-folder>`
2. `[p]load red_audio_radio`

Basic usage:

1. `[p]adbreak add <youtube-url|stream-url|/absolute/path/to/file.mp3>`
2. `[p]adbreak addjingle <youtube-url|stream-url|/absolute/path/to/file.mp3>`
3. `[p]adbreak interval 2 5`
4. `[p]adbreak adcount 1 3`
5. `[p]adbreak jinglechance 25`
6. `[p]adbreak breakjingles true`
7. `[p]adbreak volume 120`
8. `[p]adbreak toggle`
9. `[p]adbreak list`
10. `[p]adbreak list jingles`
  
Behavior notes:

1. `adbreak interval 2 5` means ad breaks trigger after a random 2 to 5 normal songs.
2. `adbreak adcount 1 3` means each break targets a random 1 to 3 ads (default is `1-3`).
3. `adbreak jinglechance 25` means there is a 25% chance to play a random standalone jingle between normal songs.
4. `adbreak breakjingles true` makes ad breaks play a random jingle at the start and end of the break.
5. `adbreak volume 120` sets the playback volume used during ad breaks, from 1 to 150, and restores the previous volume when the break ends.
6. `adbreak list` shows the ad pool only.
7. `adbreak list jingles` shows the jingle pool only.
8. Local files are accepted when you pass a filesystem path to a file Red Audio/Lavalink can decode. If a `file://` URI is provided, it is converted back to a filesystem path before lookup.

Library maintenance commands:

1. `[p]adbreak remove <number>`
2. `[p]adbreak removejingle <number>`
3. `[p]adbreak refreshmeta`
4. `[p]adbreak resetcounter`

Helpful inspection commands:

1. `[p]adbreak status`
2. `[p]adbreak preview`