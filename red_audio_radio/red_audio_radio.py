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
    def __init__(
        self,
        cog: "RedAudioRadio",
        ctx: commands.Context,
        key: str,
        label: str,
        current_page: int,
        total_pages: int,
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.key = key
        self.label = label
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
        embed = await self.cog._build_pool_list_embed(self.ctx, self.key, self.label, self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self._sync_buttons()
        embed = await self.cog._build_pool_list_embed(self.ctx, self.key, self.label, self.current_page)
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
        self._pending_break_tracks: dict[int, int] = {}
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
            break_jingles_enabled=True,
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

    def _truncate_title(self, title: str, limit: int = 60) -> str:
        title = (title or "Unknown").strip()
        if len(title) <= limit:
            return title
        return f"{title[: limit - 3].rstrip()}..."

    def _format_link_title(self, title: str, url: str, limit: int = 60) -> str:
        safe_title = self._truncate_title(title, limit).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        if url.startswith(("http://", "https://")):
            return f"[{safe_title}]({url})"
        return safe_title

    def _shuffled_entries(self, entries: list) -> list:
        shuffled_entries = list(entries)
        random.shuffle(shuffled_entries)
        return shuffled_entries

    def _format_pool_line(self, index: int, entry) -> str:
        title = self._format_link_title(self._entry_title(entry), self._entry_url(entry))
        author = self._entry_author(entry)
        if author != "Unknown":
            return f"`{index}.` {title} - {author}"
        return f"`{index}.` {title}"

    def _paginate_pool_entries(self, entries: list, label: str, max_chars: int = 900) -> list[str]:
        if not entries:
            return [f"No {label.lower()} configured."]

        pages = []
        current_lines = []
        current_length = 0

        for index, entry in enumerate(entries, start=1):
            line = self._format_pool_line(index, entry)
            line_length = len(line) + (1 if current_lines else 0)

            if current_lines and current_length + line_length > max_chars:
                pages.append("\n".join(current_lines))
                current_lines = [line]
                current_length = len(line)
                continue

            current_lines.append(line)
            current_length += line_length

        if current_lines:
            pages.append("\n".join(current_lines))

        return pages

    def _pool_page_count(self, entries: list, label: str) -> int:
        return len(self._paginate_pool_entries(entries, label))

    def _build_pool_entry(self, track_url: str, track=None) -> dict:
        return {
            "url": getattr(track, "uri", None) or track_url,
            "title": getattr(track, "title", None) or track_url,
            "author": getattr(track, "author", None) or "Unknown",
        }

    async def _refresh_pool_metadata(self, guild: discord.Guild, key: str) -> tuple[int, int, int]:
        entries = await self.config.guild(guild).get_attr(key)()
        if not entries:
            return 0, 0, 0

        refreshed_entries = []
        updated_count = 0
        failed_count = 0

        for entry in entries:
            track_url = self._entry_url(entry)
            track = await self._resolve_track(guild, track_url)
            if track is None:
                refreshed_entries.append(self._build_pool_entry(track_url))
                failed_count += 1
                continue

            new_entry = self._build_pool_entry(track_url, track)
            old_title = self._entry_title(entry)
            old_author = self._entry_author(entry)
            if new_entry["title"] != old_title or new_entry["author"] != old_author or not isinstance(entry, dict):
                updated_count += 1
            refreshed_entries.append(new_entry)

        await self.config.guild(guild).get_attr(key).set(refreshed_entries)
        return len(entries), updated_count, failed_count

    def _format_pool_page(self, entries: list, label: str, page: int) -> tuple[str, int]:
        pages = self._paginate_pool_entries(entries, label)
        total_pages = len(pages)
        page = max(1, min(page, total_pages))
        return pages[page - 1], total_pages

    async def _build_pool_list_embed(
        self, ctx: commands.Context, key: str, label: str, page: int
    ) -> discord.Embed:
        entries = await self.config.guild(ctx.guild).get_attr(key)()
        page_text, total_pages = self._format_pool_page(entries, label, page)
        current_page = max(1, min(page, total_pages))
        embed = await self._base_embed(ctx, title=f"Adbreak {label}")
        embed.add_field(name=f"{label} ({len(entries)})", value=page_text, inline=False)
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

        queued_tracks = []

        for entry in self._shuffled_entries(entries):
            track_url = self._entry_url(entry)
            track = await self._resolve_track(guild, track_url)
            if track is None:
                continue

            poster_key = self._poster_key(track)
            if poster_key and poster_key in seen_posters:
                continue
            if poster_key:
                seen_posters.add(poster_key)

            queued_tracks.append(self._build_break_track(player, guild, track, is_jingle))
            if len(queued_tracks) >= desired_count:
                break

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

        preview_tracks = []
        scanned_count = 0

        for entry in self._shuffled_entries(entries):
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

            if len(preview_tracks) >= desired_count:
                break

        return preview_tracks, scanned_count

    async def _collect_break_jingle(
        self,
        guild: discord.Guild,
        player: lavalink.Player,
    ) -> list:
        return await self._collect_pool_tracks(guild, "jingle_urls", 1, set(), player, True)

    async def _preview_break_jingle(
        self,
        guild: discord.Guild,
    ) -> list[dict]:
        tracks, _ = await self._preview_pool_tracks(guild, "jingle_urls", 1, set())
        return tracks

    async def _preview_break(self, guild: discord.Guild) -> dict:
        settings = await self.config.guild(guild).all()
        ad_count = min(len(settings["ad_urls"]), random.randint(1, 3))
        seen_posters = set()
        tracks = []
        break_jingles_enabled = (
            settings["break_jingles_enabled"] and bool(settings["jingle_urls"]) and ad_count > 0
        )

        if break_jingles_enabled:
            jingle_tracks = await self._preview_break_jingle(guild)
            for track in jingle_tracks:
                track["slot"] = "Start Jingle"
            tracks.extend(jingle_tracks)

        ad_tracks, _ = await self._preview_pool_tracks(guild, "ad_urls", ad_count, seen_posters)
        for track in ad_tracks:
            track["slot"] = "Ad"
        tracks.extend(ad_tracks)

        if break_jingles_enabled:
            closing_jingle_tracks = await self._preview_break_jingle(guild)
            for track in closing_jingle_tracks:
                track["slot"] = "End Jingle"
            tracks.extend(closing_jingle_tracks)

        standalone_jingle = bool(settings["jingle_urls"]) and random.randint(1, 100) <= settings["jingle_chance"]
        standalone_tracks = []
        if standalone_jingle:
            standalone_tracks, _ = await self._preview_pool_tracks(guild, "jingle_urls", 1, set())
            for track in standalone_tracks:
                track["slot"] = "Between-Song Jingle"

        next_break = self._pick_break_interval(
            settings["min_songs_until_break"], settings["max_songs_until_break"]
        )
        return {
            "settings": settings,
            "tracks": tracks,
            "next_break": next_break,
            "ad_count_target": ad_count,
            "standalone_jingle": standalone_jingle,
            "standalone_tracks": standalone_tracks,
            "break_jingles_enabled": break_jingles_enabled,
        }

    async def _enqueue_injected_tracks(
        self, guild: discord.Guild, player: lavalink.Player, tracks: list
    ) -> None:
        if not tracks:
            return

        self._pending_break_tracks[guild.id] = self._pending_break_tracks.get(guild.id, 0) + len(tracks)
        for break_track in reversed(tracks):
            player.queue.insert(0, break_track)

        if not player.is_playing:
            await player.play()

    async def _maybe_collect_standalone_jingle(
        self, guild: discord.Guild, settings: dict, player: lavalink.Player
    ) -> list:
        if not settings["jingle_urls"]:
            return []
        if random.randint(1, 100) > settings["jingle_chance"]:
            return []
        return await self._collect_pool_tracks(guild, "jingle_urls", 1, set(), player, True)

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
        try:
            player = lavalink.get_player(guild.id)
        except Exception:
            player = None

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

        if player is None:
            return None

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
                "Each break inserts **1-3** ads and can wrap them with jingles."
            ).format(
                status=status,
                minimum=settings["min_songs_until_break"],
                maximum=settings["max_songs_until_break"],
            ),
        )
        embed.add_field(name="Ads", value=str(len(ad_urls)), inline=True)
        embed.add_field(name="Jingles", value=str(len(jingle_urls)), inline=True)
        embed.add_field(name="Next Break", value=f"{settings['break_counter']} song(s)", inline=True)
        embed.add_field(name="Jingle Between Songs", value=f"{settings['jingle_chance']}%", inline=True)
        embed.add_field(
            name="Break Start/End Jingles",
            value="Yes" if settings["break_jingles_enabled"] else "No",
            inline=True,
        )
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
        embed.add_field(name="Jingle Between Songs", value=f"{settings['jingle_chance']}%", inline=True)
        embed.add_field(
            name="Break Start/End Jingles",
            value="Yes" if settings["break_jingles_enabled"] else "No",
            inline=True,
        )
        embed.add_field(name="Selection Mode", value="Randomized per break", inline=True)
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
        embed.add_field(
            name="Standalone Jingle Roll",
            value="Yes" if preview["standalone_jingle"] else "No",
            inline=True,
        )
        embed.add_field(
            name="Break Start/End Jingles",
            value="Yes" if preview["break_jingles_enabled"] else "No",
            inline=True,
        )
        if preview["tracks"]:
            preview_lines = []
            for index, track in enumerate(preview["tracks"], start=1):
                title = self._format_link_title(track["title"], track["uri"])
                label = track.get("slot") or ("Jingle" if track["is_jingle"] else "Ad")
                preview_lines.append(f"`{index}.` [{label}] {title} - {track['author']}")
            embed.add_field(name="Preview Tracks", value="\n".join(preview_lines[:10]), inline=False)
        else:
            embed.add_field(name="Preview Tracks", value="No playable tracks would be selected right now.", inline=False)

        if preview["standalone_tracks"]:
            standalone_lines = []
            for index, track in enumerate(preview["standalone_tracks"], start=1):
                title = self._format_link_title(track["title"], track["uri"])
                standalone_lines.append(f"`{index}.` [{track.get('slot', 'Jingle')}] {title} - {track['author']}")
            embed.add_field(name="Between-Song Jingle", value="\n".join(standalone_lines), inline=False)
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
        """Set the percent chance a random jingle plays between normal songs."""
        if chance < 0 or chance > 100:
            return await ctx.send("Chance must be between 0 and 100.")
        await self.config.guild(ctx.guild).jingle_chance.set(chance)
        await ctx.send(f"Between-song jingle chance set to {chance}%.")

    @adbreak.command(name="breakjingles")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_breakjingles(self, ctx: commands.Context, enabled: Optional[bool] = None):
        """Toggle start/end jingles for ad breaks."""
        current = await self.config.guild(ctx.guild).break_jingles_enabled()
        if enabled is None:
            enabled = not current
        await self.config.guild(ctx.guild).break_jingles_enabled.set(enabled)
        await ctx.send(f"Break start/end jingles are now {'enabled' if enabled else 'disabled'}.")

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

    @adbreak.command(name="refreshmeta")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_refreshmeta(self, ctx: commands.Context):
        """Refresh saved titles and authors for all ads and jingles."""
        ad_total, ad_updated, ad_failed = await self._refresh_pool_metadata(ctx.guild, "ad_urls")
        jingle_total, jingle_updated, jingle_failed = await self._refresh_pool_metadata(
            ctx.guild, "jingle_urls"
        )

        embed = await self._base_embed(
            ctx,
            title="Metadata Refresh Complete",
            description="Saved adbreak titles and authors have been refreshed.",
        )
        embed.add_field(
            name="Ads",
            value=(
                f"Checked: {ad_total}\n"
                f"Updated: {ad_updated}\n"
                f"Failed to resolve: {ad_failed}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Jingles",
            value=(
                f"Checked: {jingle_total}\n"
                f"Updated: {jingle_updated}\n"
                f"Failed to resolve: {jingle_failed}"
            ),
            inline=True,
        )
        await ctx.send(embed=embed)

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

    @adbreak.group(name="list", invoke_without_command=True)
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_list(self, ctx: commands.Context, page: int = 1):
        """List configured ads."""
        embed = await self._build_pool_list_embed(ctx, "ad_urls", "Ads", page)
        ad_entries = await self.config.guild(ctx.guild).ad_urls()
        total_pages = self._pool_page_count(ad_entries, "Ads")
        current_page = max(1, min(page, total_pages))
        view = AdbreakListView(self, ctx, "ad_urls", "Ads", current_page, total_pages)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @adbreak_list.command(name="jingles")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def adbreak_list_jingles(self, ctx: commands.Context, page: int = 1):
        """List configured jingles."""
        embed = await self._build_pool_list_embed(ctx, "jingle_urls", "Jingles", page)
        jingle_entries = await self.config.guild(ctx.guild).jingle_urls()
        total_pages = self._pool_page_count(jingle_entries, "Jingles")
        current_page = max(1, min(page, total_pages))
        view = AdbreakListView(self, ctx, "jingle_urls", "Jingles", current_page, total_pages)
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

        pending_break_tracks = self._pending_break_tracks.get(guild.id, 0)
        if pending_break_tracks > 0:
            remaining_tracks = pending_break_tracks - 1
            if remaining_tracks > 0:
                self._pending_break_tracks[guild.id] = remaining_tracks
            else:
                self._pending_break_tracks.pop(guild.id, None)
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
        break_triggered = False

        if min_songs_until_break > 0 and max_songs_until_break > 0:
            break_counter -= 1
            if break_counter > 0:
                await self.config.guild(guild).break_counter.set(break_counter)
            else:
                break_triggered = True
                next_break = self._pick_break_interval(min_songs_until_break, max_songs_until_break)
                await self.config.guild(guild).break_counter.set(next_break)

        if not break_triggered:
            standalone_tracks = await self._maybe_collect_standalone_jingle(guild, settings, player)
            await self._enqueue_injected_tracks(guild, player, standalone_tracks)
            return

        ad_count = min(len(settings["ad_urls"]), random.randint(1, 3))
        if ad_count <= 0:
            return

        break_jingles_enabled = settings["break_jingles_enabled"] and bool(settings["jingle_urls"])

        queued_tracks = []
        seen_posters = set()

        if break_jingles_enabled:
            queued_tracks.extend(await self._collect_break_jingle(guild, player))

        queued_tracks.extend(
            await self._collect_pool_tracks(
                guild, "ad_urls", ad_count, seen_posters, player, False
            )
        )

        if break_jingles_enabled:
            queued_tracks.extend(await self._collect_break_jingle(guild, player))

        if not queued_tracks:
            return

        await self._enqueue_injected_tracks(guild, player, queued_tracks)

    @commands.command()
    async def mycom(self, ctx):
        """Send a test response."""
        await ctx.send("I can do stuff!")