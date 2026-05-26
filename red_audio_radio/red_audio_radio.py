from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Optional

import discord
import lavalink

from redbot.core import Config, commands

try:
    from redbot.cogs.audio.audio_dataclasses import Query
except Exception:  # pragma: no cover - audio may not be loaded yet
    Query = None


class RedAudioRadio(commands.Cog):
    """Injects configured ad or jingle tracks between audio songs."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2310625601, force_registration=True)
        self.config.register_guild(
            enabled=False,
            ad_urls=[],
            jingle_urls=[],
            ad_cursor=0,
            jingle_cursor=0,
            songs_until_break=0,
            break_counter=0,
            jingle_chance=25,
        )

    def _audio_cog(self):
        return self.bot.get_cog("Audio")

    async def _next_urls(self, guild: discord.Guild, key: str, count: int) -> list[str]:
        urls = await self.config.guild(guild).get_attr(key)()
        if not urls or count <= 0:
            return []

        cursor_key = "ad_cursor" if key == "ad_urls" else "jingle_cursor"
        cursor = await self.config.guild(guild).get_attr(cursor_key)()
        selected = []

        for offset in range(count):
            selected.append(urls[(cursor + offset) % len(urls)])

        await self.config.guild(guild).get_attr(cursor_key).set((cursor + count) % len(urls))
        return selected

    def _build_break_track(self, player: lavalink.Player, guild: discord.Guild, track, is_jingle: bool):
        track.extras.update(
            {
                "red_audio_radio_ad": True,
                "red_audio_radio_jingle": is_jingle,
                "enqueue_time": 0,
                "vc": player.channel.id,
                "requester": guild.me.id,
            }
        )
        track.requester = guild.me
        return track

    async def _resolve_track(self, guild: discord.Guild, track_url: str):
        audio_cog = self._audio_cog()
        player = lavalink.get_player(guild.id)

        if audio_cog is not None and getattr(audio_cog, "api_interface", None) is not None:
            if Query is not None:
                query = Query.process_input(track_url, audio_cog.local_folder_current_path)
            else:
                query = track_url
            ctx = SimpleNamespace(
                guild=guild,
                message=SimpleNamespace(guild=guild),
                cog=audio_cog,
            )
            try:
                result, _ = await audio_cog.api_interface.fetch_track(
                    ctx, player, query, forced=True, lazy=True
                )
                if result and getattr(result, "tracks", None):
                    return result.tracks[0]
            except Exception:
                pass

        result = await player.load_tracks(track_url)
        if result and getattr(result, "tracks", None):
            return result.tracks[0]
        return None

    @commands.group(name="adbreak", invoke_without_command=True)
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak(self, ctx: commands.Context):
        """Manage audio ads and jingles that play between songs."""
        settings = await self.config.guild(ctx.guild).all()
        status = "enabled" if settings["enabled"] else "disabled"
        ad_urls = settings["ad_urls"]
        jingle_urls = settings["jingle_urls"]
        await ctx.send(
            "Ad breaks are {status}. Ads: {ads}. Jingles: {jingles}. Interval: {interval} song(s). Each break inserts 1-3 ads.".format(
                status=status,
                ads=len(ad_urls),
                jingles=len(jingle_urls),
                interval=settings["songs_until_break"],
            )
        )

    @adbreak.command(name="toggle")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_toggle(self, ctx: commands.Context):
        """Toggle automatic break insertion."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        await ctx.send(f"Breaks are now {'enabled' if not current else 'disabled'}.")

    @adbreak.command(name="interval")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_interval(self, ctx: commands.Context, songs: int):
        """Set how many songs play before a break occurs. Set 0 to disable."""
        if songs < 0:
            return await ctx.send("Interval must be 0 or greater.")
        await self.config.guild(ctx.guild).songs_until_break.set(songs)
        await self.config.guild(ctx.guild).break_counter.set(songs)
        await ctx.send(f"Break interval set to {songs} song(s).")

    @adbreak.command(name="jinglechance")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_jinglechance(self, ctx: commands.Context, chance: int):
        """Set the percent chance a break uses a jingle instead of an ad."""
        if chance < 0 or chance > 100:
            return await ctx.send("Chance must be between 0 and 100.")
        await self.config.guild(ctx.guild).jingle_chance.set(chance)
        await ctx.send(f"Jingle chance set to {chance}%.")

    @adbreak.command(name="add")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_add(self, ctx: commands.Context, *, ad_url: str):
        """Add an ad track URL or search URL."""
        ad_urls = await self.config.guild(ctx.guild).ad_urls()
        ad_urls.append(ad_url)
        await self.config.guild(ctx.guild).ad_urls.set(ad_urls)
        await ctx.send(f"Stored ad #{len(ad_urls)}.")

    @adbreak.command(name="addjingle")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_addjingle(self, ctx: commands.Context, *, jingle_url: str):
        """Add a station jingle URL or search URL."""
        jingle_urls = await self.config.guild(ctx.guild).jingle_urls()
        jingle_urls.append(jingle_url)
        await self.config.guild(ctx.guild).jingle_urls.set(jingle_urls)
        await ctx.send(f"Stored jingle #{len(jingle_urls)}.")

    @adbreak.command(name="remove")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_remove(self, ctx: commands.Context, index: int):
        """Remove an ad by its list number."""
        ad_urls = await self.config.guild(ctx.guild).ad_urls()
        if index < 1 or index > len(ad_urls):
            return await ctx.send("That ad number does not exist.")
        removed = ad_urls.pop(index - 1)
        await self.config.guild(ctx.guild).ad_urls.set(ad_urls)
        await ctx.send(f"Removed ad #{index}: {removed}")

    @adbreak.command(name="removejingle")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_removejingle(self, ctx: commands.Context, index: int):
        """Remove a jingle by its list number."""
        jingle_urls = await self.config.guild(ctx.guild).jingle_urls()
        if index < 1 or index > len(jingle_urls):
            return await ctx.send("That jingle number does not exist.")
        removed = jingle_urls.pop(index - 1)
        await self.config.guild(ctx.guild).jingle_urls.set(jingle_urls)
        await ctx.send(f"Removed jingle #{index}: {removed}")

    @adbreak.command(name="list")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_list(self, ctx: commands.Context):
        """List configured ad URLs and jingles."""
        settings = await self.config.guild(ctx.guild).all()
        ad_urls = settings["ad_urls"]
        jingle_urls = settings["jingle_urls"]
        lines = ["Ads:"]
        if ad_urls:
            lines.extend(f"{idx}. {url}" for idx, url in enumerate(ad_urls, start=1))
        else:
            lines.append("None configured.")
        lines.append("")
        lines.append("Jingles:")
        if jingle_urls:
            lines.extend(f"{idx}. {url}" for idx, url in enumerate(jingle_urls, start=1))
        else:
            lines.append("None configured.")
        await ctx.send("\n".join(lines))

    @adbreak.command(name="resetcounter")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_resetcounter(self, ctx: commands.Context):
        """Reset the break countdown to the configured interval."""
        songs_until_break = await self.config.guild(ctx.guild).songs_until_break()
        await self.config.guild(ctx.guild).break_counter.set(songs_until_break)
        await ctx.send("Break counter reset.")

    @commands.Cog.listener()
    async def on_red_audio_track_end(
        self, guild: discord.Guild, track: lavalink.Track, requester: discord.Member
    ):
        if not guild or not track:
            return

        extras = getattr(track, "extras", {}) or {}
        if extras.get("red_audio_radio_ad") or extras.get("red_audio_radio_jingle"):
            return

        if getattr(requester, "id", None) == getattr(guild.me, "id", None):
            return

        settings = await self.config.guild(guild).all()
        if not settings["enabled"]:
            return

        player = lavalink.get_player(guild.id)
        if not player.queue:
            return

        songs_until_break = settings["songs_until_break"]
        break_counter = settings["break_counter"]

        if songs_until_break <= 0:
            return

        break_counter -= 1
        if break_counter > 0:
            await self.config.guild(guild).break_counter.set(break_counter)
            return

        await self.config.guild(guild).break_counter.set(songs_until_break)

        use_jingle = bool(settings["jingle_urls"]) and (
            not settings["ad_urls"] or random.randint(1, 100) <= settings["jingle_chance"]
        )

        ad_count = min(len(settings["ad_urls"]), random.randint(1, 3))
        ad_urls = await self._next_urls(guild, "ad_urls", ad_count)
        if not ad_urls and not settings["jingle_urls"]:
            return

        queued_tracks = []

        if use_jingle:
            jingle_urls = await self._next_urls(guild, "jingle_urls", 1)
            for jingle_url in jingle_urls:
                jingle_track = await self._resolve_track(guild, jingle_url)
                if jingle_track is not None:
                    queued_tracks.append(self._build_break_track(player, guild, jingle_track, True))

        for ad_url in ad_urls:
            ad_track = await self._resolve_track(guild, ad_url)
            if ad_track is not None:
                queued_tracks.append(self._build_break_track(player, guild, ad_track, False))

        if not queued_tracks:
            return

        for break_track in reversed(queued_tracks):
            player.queue.insert(0, break_track)

        if not player.is_playing:
            await player.play()

    @commands.command()
    async def mycom(self, ctx):
        """Send a test response."""
        await ctx.send("I can do stuff!")