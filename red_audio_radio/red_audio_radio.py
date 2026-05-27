from __future__ import annotations

import contextlib
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


class AdbreakListView(discord.ui.View):
    def __init__(self, cog: "RedAudioRadio", ctx: commands.Context, current_page: int, total_pages: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.current_page = current_page
        self.total_pages = total_pages
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous_page.disabled = self.current_page <= 1
        self.next_page.disabled = self.current_page >= self.total_pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the command invoker can use these controls.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self._sync_buttons()
        embed = await self.cog._build_library_embed(self.ctx, self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self._sync_buttons()
        embed = await self.cog._build_library_embed(self.ctx, self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


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
            min_songs_until_break=0,
            max_songs_until_break=0,
            break_counter=0,
            jingle_chance=25,
        )

    def _audio_cog(self):
        return self.bot.get_cog("Audio")

    async def _base_embed(self, ctx: commands.Context, title: str, description: str | None = None):
        embed = discord.Embed(
            title=title,
            description=description,
            colour=await ctx.embed_colour(),
        )
        if ctx.guild is not None:
            embed.set_footer(text=f"{ctx.guild.name}")
        return embed

    def _entry_url(self, entry) -> str:
        if isinstance(entry, dict):
            return entry.get("url", "")
        return str(entry)

    def _entry_title(self, entry) -> str:
        if isinstance(entry, dict):
            return entry.get("title") or self._entry_url(entry)
        return str(entry)

    def _entry_author(self, entry) -> str:
        if isinstance(entry, dict):
            return entry.get("author") or "Unknown"
        return "Unknown"

    def _build_pool_entry(self, track_url: str, track=None) -> dict:
        return {
            "url": getattr(track, "uri", None) or track_url,
            "title": getattr(track, "title", None) or track_url,
            "author": getattr(track, "author", None) or "Unknown",
        }

    def _format_pool_page(self, entries: list, label: str, page: int, per_page: int = 10) -> tuple[str, int]:
        if not entries:
            return f"No {label.lower()} configured.", 1

        total_pages = max(1, (len(entries) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start_index = (page - 1) * per_page
        page_entries = entries[start_index : start_index + per_page]

        lines = []
        for index, entry in enumerate(page_entries, start=start_index + 1):
            title = self._entry_title(entry)
            author = self._entry_author(entry)
            if author != "Unknown":
                lines.append(f"`{index}.` {title} - {author}")
            else:
                lines.append(f"`{index}.` {title}")

        return "\n".join(lines), total_pages

    async def _build_library_embed(self, ctx: commands.Context, page: int) -> discord.Embed:
        settings = await self.config.guild(ctx.guild).all()
        ad_entries = settings["ad_urls"]
        jingle_entries = settings["jingle_urls"]
        ad_text, ad_pages = self._format_pool_page(ad_entries, "Ads", page)
        jingle_text, jingle_pages = self._format_pool_page(jingle_entries, "Jingles", page)
        total_pages = max(ad_pages, jingle_pages)
        current_page = max(1, min(page, total_pages))
        embed = await self._base_embed(ctx, title="Adbreak Library")
        embed.add_field(name=f"Ads ({len(ad_entries)})", value=ad_text, inline=False)
        embed.add_field(name=f"Jingles ({len(jingle_entries)})", value=jingle_text, inline=False)
        embed.set_footer(text=f"{ctx.guild.name} | Page {current_page}/{total_pages}")
        return embed

    def _pick_break_interval(self, minimum: int, maximum: int) -> int:
        if minimum <= 0 or maximum <= 0:
            return 0
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        return random.randint(minimum, maximum)

    def _cursor_key_for_pool(self, key: str) -> str:
        return "ad_cursor" if key == "ad_urls" else "jingle_cursor"

    async def _pool_urls(self, guild: discord.Guild, key: str) -> list[str]:
        return await self.config.guild(guild).get_attr(key)()

    async def _pool_cursor(self, guild: discord.Guild, key: str) -> int:
        return await self.config.guild(guild).get_attr(self._cursor_key_for_pool(key))()

    async def _collect_pool_tracks(
        self,
        guild: discord.Guild,
        key: str,
        desired_count: int,
        seen_posters: set[str],
        player: lavalink.Player,
        is_jingle: bool,
    ) -> list:
        entries = await self.config.guild(guild).get_attr(key)()
        if not entries or desired_count <= 0:
            return []

        cursor_key = "ad_cursor" if key == "ad_urls" else "jingle_cursor"
        cursor = await self.config.guild(guild).get_attr(cursor_key)()
        queued_tracks = []
        scanned_count = 0

        while scanned_count < len(entries) and len(queued_tracks) < desired_count:
            entry = entries[(cursor + scanned_count) % len(entries)]
            track_url = self._entry_url(entry)
            scanned_count += 1
            track = await self._resolve_track(guild, track_url)
            if track is None:
                continue

            poster_key = self._poster_key(track)
            if poster_key and poster_key in seen_posters:
                continue
            if poster_key:
                seen_posters.add(poster_key)

            queued_tracks.append(self._build_break_track(player, guild, track, is_jingle))

        await self.config.guild(guild).get_attr(cursor_key).set((cursor + scanned_count) % len(entries))
        return queued_tracks

    async def _preview_pool_tracks(
        self,
        guild: discord.Guild,
        key: str,
        desired_count: int,
        seen_posters: set[str],
    ) -> tuple[list[dict], int]:
        entries = await self._pool_urls(guild, key)
        if not entries or desired_count <= 0:
            return [], 0

        cursor = await self._pool_cursor(guild, key)
        preview_tracks = []
        scanned_count = 0

        while scanned_count < len(entries) and len(preview_tracks) < desired_count:
            entry = entries[(cursor + scanned_count) % len(entries)]
            track_url = self._entry_url(entry)
            scanned_count += 1
            track = await self._resolve_track(guild, track_url)
            if track is None:
                continue

            poster_key = self._poster_key(track)
            if poster_key and poster_key in seen_posters:
                continue
            if poster_key:
                seen_posters.add(poster_key)

            preview_tracks.append(
                {
                    "title": getattr(track, "title", None) or self._entry_title(entry),
                    "author": getattr(track, "author", None) or self._entry_author(entry),
                    "uri": getattr(track, "uri", None) or self._entry_url(entry),
                    "is_jingle": key == "jingle_urls",
                }
            )

        return preview_tracks, scanned_count

    async def _preview_break(self, guild: discord.Guild) -> dict:
        settings = await self.config.guild(guild).all()
        use_jingle = bool(settings["jingle_urls"]) and (
            not settings["ad_urls"] or random.randint(1, 100) <= settings["jingle_chance"]
        )
        ad_count = min(len(settings["ad_urls"]), random.randint(1, 3))
        seen_posters = set()
        tracks = []

        if use_jingle:
            jingle_tracks, _ = await self._preview_pool_tracks(guild, "jingle_urls", 1, seen_posters)
            tracks.extend(jingle_tracks)

        ad_tracks, _ = await self._preview_pool_tracks(guild, "ad_urls", ad_count, seen_posters)
        tracks.extend(ad_tracks)

        next_break = self._pick_break_interval(
            settings["min_songs_until_break"], settings["max_songs_until_break"]
        )
        return {
            "settings": settings,
            "tracks": tracks,
            "next_break": next_break,
            "ad_count_target": ad_count,
            "use_jingle": use_jingle,
        }

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

    def _poster_key(self, track) -> str:
        author = (getattr(track, "author", None) or "").strip().casefold()
        if author:
            return author

        uri = (getattr(track, "uri", None) or "").strip().casefold()
        return uri

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
        embed = await self._base_embed(
            ctx,
            title="Adbreak Overview",
            description=(
                "Ad breaks are **{status}**. Interval: **{minimum}-{maximum}** song(s). "
                "Each break inserts **1-3** ads."
            ).format(
                status=status,
                minimum=settings["min_songs_until_break"],
                maximum=settings["max_songs_until_break"],
            ),
        )
        embed.add_field(name="Ads", value=str(len(ad_urls)), inline=True)
        embed.add_field(name="Jingles", value=str(len(jingle_urls)), inline=True)
        embed.add_field(name="Next Break", value=f"{settings['break_counter']} song(s)", inline=True)
        await ctx.send(embed=embed)

    @adbreak.command(name="status")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_status(self, ctx: commands.Context):
        """Show current adbreak settings and runtime status."""
        settings = await self.config.guild(ctx.guild).all()
        try:
            player = lavalink.get_player(ctx.guild.id)
        except Exception:
            player = None
        embed = await self._base_embed(ctx, title="Adbreak Status")
        embed.add_field(name="Enabled", value="Yes" if settings["enabled"] else "No", inline=True)
        embed.add_field(
            name="Interval Range",
            value=f"{settings['min_songs_until_break']}-{settings['max_songs_until_break']} song(s)",
            inline=True,
        )
        embed.add_field(name="Next Break", value=f"{settings['break_counter']} song(s)", inline=True)
        embed.add_field(name="Ads Stored", value=str(len(settings["ad_urls"])), inline=True)
        embed.add_field(name="Jingles Stored", value=str(len(settings["jingle_urls"])), inline=True)
        embed.add_field(name="Jingle Chance", value=f"{settings['jingle_chance']}%", inline=True)
        embed.add_field(name="Ad Cursor", value=str(settings["ad_cursor"]), inline=True)
        embed.add_field(name="Jingle Cursor", value=str(settings["jingle_cursor"]), inline=True)
        if player is not None:
            now_playing = getattr(player, "current", None)
            embed.add_field(name="Currently Playing", value="Yes" if now_playing else "No", inline=True)
            embed.add_field(name="Queued Tracks", value=str(len(getattr(player, "queue", []))), inline=True)
        await ctx.send(embed=embed)

    @adbreak.command(name="preview")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_preview(self, ctx: commands.Context):
        """Preview the next adbreak selection without changing cursors or queue."""
        preview = await self._preview_break(ctx.guild)
        settings = preview["settings"]
        embed = await self._base_embed(
            ctx,
            title="Adbreak Preview",
            description="Simulation only. This does not change cursors or queue state.",
        )
        embed.add_field(name="Current Countdown", value=f"{settings['break_counter']} song(s)", inline=True)
        embed.add_field(
            name="Configured Interval",
            value=f"{settings['min_songs_until_break']}-{settings['max_songs_until_break']} song(s)",
            inline=True,
        )
        embed.add_field(name="Next Countdown", value=f"{preview['next_break']} song(s)", inline=True)
        embed.add_field(name="Target Ads", value=str(preview["ad_count_target"]), inline=True)
        embed.add_field(name="Jingle Selected", value="Yes" if preview["use_jingle"] else "No", inline=True)
        if preview["tracks"]:
            preview_lines = []
            for index, track in enumerate(preview["tracks"], start=1):
                kind = "Jingle" if track["is_jingle"] else "Ad"
                preview_lines.append(f"`{index}.` [{kind}] {track['title']} - {track['author']}")
            embed.add_field(name="Preview Tracks", value="\n".join(preview_lines[:10]), inline=False)
        else:
            embed.add_field(name="Preview Tracks", value="No playable tracks would be selected right now.", inline=False)
        await ctx.send(embed=embed)

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
    async def adbreak_interval(self, ctx: commands.Context, minimum: int, maximum: Optional[int] = None):
        """Set a random song interval range for breaks. Use one value for a fixed interval."""
        if maximum is None:
            maximum = minimum
        if minimum < 0 or maximum < 0:
            return await ctx.send("Interval values must be 0 or greater.")
        if minimum > maximum:
            minimum, maximum = maximum, minimum

        next_break = self._pick_break_interval(minimum, maximum)
        await self.config.guild(ctx.guild).min_songs_until_break.set(minimum)
        await self.config.guild(ctx.guild).max_songs_until_break.set(maximum)
        await self.config.guild(ctx.guild).break_counter.set(next_break)
        await ctx.send(f"Break interval set to {minimum}-{maximum} song(s). Next break in {next_break} song(s).")

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
        ad_entries = await self.config.guild(ctx.guild).ad_urls()
        track = await self._resolve_track(ctx.guild, ad_url)
        ad_entries.append(self._build_pool_entry(ad_url, track))
        await self.config.guild(ctx.guild).ad_urls.set(ad_entries)
        await ctx.send(f"Stored ad #{len(ad_entries)}: {self._entry_title(ad_entries[-1])}")

    @adbreak.command(name="addjingle")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_addjingle(self, ctx: commands.Context, *, jingle_url: str):
        """Add a station jingle URL or search URL."""
        jingle_entries = await self.config.guild(ctx.guild).jingle_urls()
        track = await self._resolve_track(ctx.guild, jingle_url)
        jingle_entries.append(self._build_pool_entry(jingle_url, track))
        await self.config.guild(ctx.guild).jingle_urls.set(jingle_entries)
        await ctx.send(f"Stored jingle #{len(jingle_entries)}: {self._entry_title(jingle_entries[-1])}")

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
        await ctx.send(f"Removed ad #{index}: {self._entry_title(removed)}")

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
        await ctx.send(f"Removed jingle #{index}: {self._entry_title(removed)}")

    @adbreak.command(name="list")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_list(self, ctx: commands.Context, page: int = 1):
        """List configured ad URLs and jingles."""
        embed = await self._build_library_embed(ctx, page)
        settings = await self.config.guild(ctx.guild).all()
        ad_pages = max(1, (len(settings["ad_urls"]) + 9) // 10)
        jingle_pages = max(1, (len(settings["jingle_urls"]) + 9) // 10)
        total_pages = max(ad_pages, jingle_pages)
        current_page = max(1, min(page, total_pages))
        view = AdbreakListView(self, ctx, current_page, total_pages)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @adbreak.command(name="resetcounter")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_resetcounter(self, ctx: commands.Context):
        """Reset the break countdown using the configured interval range."""
        settings = await self.config.guild(ctx.guild).all()
        next_break = self._pick_break_interval(
            settings["min_songs_until_break"], settings["max_songs_until_break"]
        )
        await self.config.guild(ctx.guild).break_counter.set(next_break)
        await ctx.send(f"Break counter reset. Next break in {next_break} song(s).")

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

        min_songs_until_break = settings["min_songs_until_break"]
        max_songs_until_break = settings["max_songs_until_break"]
        break_counter = settings["break_counter"]

        if min_songs_until_break <= 0 or max_songs_until_break <= 0:
            return

        break_counter -= 1
        if break_counter > 0:
            await self.config.guild(guild).break_counter.set(break_counter)
            return

        next_break = self._pick_break_interval(min_songs_until_break, max_songs_until_break)
        await self.config.guild(guild).break_counter.set(next_break)

        use_jingle = bool(settings["jingle_urls"]) and (
            not settings["ad_urls"] or random.randint(1, 100) <= settings["jingle_chance"]
        )

        ad_count = min(len(settings["ad_urls"]), random.randint(1, 3))
        if ad_count <= 0 and not settings["jingle_urls"]:
            return

        queued_tracks = []
        seen_posters = set()

        if use_jingle:
            queued_tracks.extend(
                await self._collect_pool_tracks(
                    guild, "jingle_urls", 1, seen_posters, player, True
                )
            )

        queued_tracks.extend(
            await self._collect_pool_tracks(
                guild, "ad_urls", ad_count, seen_posters, player, False
            )
        )

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