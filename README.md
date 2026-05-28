# red-audio-radio

Red V3 cog for inserting configurable ads and jingles around Red Audio playback.

Package path: `red_audio_radio/`

Load it in Red with:

1. `[p]addpath <this-repo-folder>`
2. `[p]load red_audio_radio`

Basic usage:

1. `[p]adbreak add <youtube-or-stream-url>`
2. `[p]adbreak addjingle <youtube-or-stream-url>`
3. `[p]adbreak interval 2 5`
4. `[p]adbreak jinglechance 25`
5. `[p]adbreak breakjingles true`
6. `[p]adbreak volume 120`
7. `[p]adbreak toggle`
8. `[p]adbreak list`
9. `[p]adbreak list jingles`
  
Behavior notes:

1. `adbreak interval 2 5` means ad breaks trigger after a random 2 to 5 normal songs.
2. `adbreak jinglechance 25` means there is a 25% chance to play a random standalone jingle between normal songs.
3. `adbreak breakjingles true` makes ad breaks play a random jingle at the start and end of the break.
4. `adbreak volume 120` sets the playback volume used during ad breaks, from 1 to 150, and restores the previous volume when the break ends.
5. `adbreak list` shows the ad pool only.
6. `adbreak list jingles` shows the jingle pool only.

Library maintenance commands:

1. `[p]adbreak remove <number>`
2. `[p]adbreak removejingle <number>`
3. `[p]adbreak refreshmeta`
4. `[p]adbreak resetcounter`

Helpful inspection commands:

1. `[p]adbreak status`
2. `[p]adbreak preview`