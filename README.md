# red-audio-radio

Red V3 cog for inserting configurable ad tracks between songs.

Package path: `red_audio_radio/`

Load it in Red with:

1. `[p]addpath <this-repo-folder>`
2. `[p]load red_audio_radio`

Basic usage:

1. `[p]adbreak add <youtube-or-stream-url>`
2. `[p]adbreak addjingle <youtube-or-stream-url>`
3. `[p]adbreak interval 2 5`
4. `[p]adbreak jinglechance 25`
5. `[p]adbreak toggle`
6. `[p]adbreak list`

Library maintenance commands:

1. `[p]adbreak remove <number>`
2. `[p]adbreak removejingle <number>`
3. `[p]adbreak refreshmeta`
4. `[p]adbreak resetcounter`

Helpful inspection commands:

1. `[p]adbreak status`
2. `[p]adbreak preview`