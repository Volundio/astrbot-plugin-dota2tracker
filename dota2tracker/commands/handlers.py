"""Command use cases (ports of ``app/commands/*.command.ts``).

Every command is an async generator yielding :class:`OutgoingMessage` objects;
``main.py`` converts them to AstrBot results. Tips ("正在搜索对局详情…") go
through a :class:`TipMessenger` so they can be recalled on OneBot.

Deliberate deviations from the Koishi version are marked "Target fix" and are
listed in DEVELOPMENT.md §9.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ..application.binding import STEAM_ID_PATTERN, BindingService
from ..application.library import HeroService, ItemService, RecommendationService
from ..application.match import MatchService
from ..application.models import ChatContext, OutgoingMessage, Subscriber
from ..application.players import PlayerService
from ..config import MATCH_TEMPLATES, PluginConfig
from ..errors import ErrorReporter
from ..i18n.service import I18nService
from ..presentation.images import ImageFactory
from ..presentation.messages import MessageBuilder
from ..presentation.renderer import RenderError
from ..utils.jsnum import has_line_terminator, utf16_length
from ..utils.timefmt import local_now
from .args import parse_args
from .registry import COMMANDS, COMMANDS_BY_KEY, GROUP, CommandSpec, option_entries, wants_help

__all__ = ["CommandHandlers", "PendingQueue", "TipMessenger"]

Reply = AsyncIterator[OutgoingMessage]
COMMON = "commands.dota2tracker.common.messages."


class TipMessenger(Protocol):
    async def send(self, text: str) -> None: ...

    async def finish(self) -> None: ...


class PendingQueue(Protocol):
    async def add(self, match_id: int, subscribers: list[Subscriber]) -> None: ...


@dataclass
class _Context:
    chat: ChatContext
    locale: str
    tips: TipMessenger
    key: str

    def message_key(self, name: str) -> str:
        return f"commands.dota2tracker.{self.key}.messages.{name}"


def _valid_nickname(value: str) -> bool:
    """``/^.{1,20}$/`` – JS ``.`` excludes line terminators and counts UTF-16 units."""
    return 1 <= utf16_length(value) <= 20 and not has_line_terminator(value)


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    number = (
        value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default
    )
    return min(max(number, low), high)


class CommandHandlers:
    def __init__(
        self,
        *,
        config: PluginConfig,
        i18n: I18nService,
        bindings: BindingService,
        matches: MatchService,
        players: PlayerService,
        heroes: HeroService,
        items: ItemService,
        recommendations: RecommendationService,
        images: ImageFactory,
        messages: MessageBuilder,
        pending: PendingQueue,
        reporter: ErrorReporter,
        clock: Callable[[], datetime] = local_now,
    ) -> None:
        self.config = config
        self.i18n = i18n
        self.bindings = bindings
        self.matches = matches
        self.players = players
        self.heroes = heroes
        self.items = items
        self.recommendations = recommendations
        self.images = images
        self.messages = messages
        self.pending = pending
        self.reporter = reporter
        self.clock = clock

    # ------------------------------------------------------------- helpers
    async def locale_for(self, chat: ChatContext) -> str:
        channel_locale = None
        if not chat.is_private:
            channel_locale = await self.bindings.channel_locale(chat.platform_id, chat.channel_id)
        return self.i18n.resolve_locale(channel_locale)

    def _text(self, ctx: _Context, key: str, params: Any = None) -> OutgoingMessage:
        full = key if key.startswith("commands.") else ctx.message_key(key.lstrip("."))
        return OutgoingMessage.of(self.i18n.text(ctx.locale, full, params))

    async def _context(self, chat: ChatContext, key: str, tips: TipMessenger) -> _Context:
        return _Context(chat, await self.locale_for(chat), tips, key)

    async def _resolve_player(self, ctx: _Context, value: str | None) -> tuple[int | None, OutgoingMessage | None]:
        """``resolvePlayerAndHandleErrors``."""
        if ctx.chat.is_private and not value:
            return None, self._text(ctx, COMMON + "user_not_in_group")
        result = await self.bindings.resolve_steam_id(ctx.chat, value)
        if result.success:
            return result.steam_id, None
        if result.reason == "NOT_BINDED":
            return None, self._text(ctx, COMMON + "user_not_binded_in_channel")
        return None, self._text(ctx, COMMON + "invalid_input_include_steam_id")

    def _render_failed(self, ctx: _Context, error: BaseException) -> None:
        self.reporter.report(error, f"command/{ctx.key}")

    async def run(self, key: str, chat: ChatContext, raw: str, tips: TipMessenger) -> Reply:
        """Dispatch ``key`` (registry key) with the raw argument text.

        ``-h`` / ``--help`` prints the command help like Koishi did. Any error
        a handler does not turn into a message itself (storage unavailable
        during a reload, …) is logged and answered with a generic text instead
        of reaching the chat as a raw exception (target fix).
        """
        spec = COMMANDS_BY_KEY[key]
        locale = self.i18n.default_locale
        try:
            ctx = await self._context(chat, key, tips)
            locale = ctx.locale
            parsed = parse_args(raw, spec.options)
            if wants_help(parsed.options):
                yield OutgoingMessage.of(self.command_help(spec, locale))
                return
            handler: Callable[..., AsyncIterator[OutgoingMessage]] = getattr(self, "cmd_" + key.replace("-", "_"))
            async for message in handler(ctx, parsed.args, parsed.options):
                yield message
        except Exception as error:
            self.reporter.report(error, f"command/{key}")
            yield OutgoingMessage.of(self.i18n.text(locale, "dota2tracker.command_failed"))

    async def root(self, chat: ChatContext) -> Reply:
        """``dota2tracker`` (with or without ``-h``): Koishi's sub-command overview."""
        try:
            locale = await self.locale_for(chat)
        except Exception as error:
            self.reporter.report(error, "command/root")
            locale = self.i18n.default_locale
        yield OutgoingMessage.of(self.root_help(locale))

    # ------------------------------------------------------------ help text
    def _raw(self, locale: str, key: str) -> str:
        value = self.i18n.raw(locale, key)
        return value if isinstance(value, str) else ""

    def command_help(self, spec: CommandSpec, locale: str) -> str:
        """Plain-text help of one command (Koishi ``<command> -h``)."""
        label = "dota2tracker.cli_help."
        lines = [self.i18n.text(locale, label + "command", [f"{spec.legacy_name} {spec.arguments}".strip()])]
        if spec.aliases:
            lines.append(self.i18n.text(locale, label + "aliases", ["、".join(spec.aliases)]))
        for part in ("description", "usage"):
            text = self.i18n.t(locale, f"{spec.i18n_prefix}.{part}")
            if text:
                lines.append(text)
        options = option_entries(lambda key: self._raw(locale, key), spec)
        if options:
            lines.append(self.i18n.text(locale, label + "options"))
            lines.extend(f"    {syntax}  {description}" for syntax, description in options)
        examples = self.i18n.t(locale, f"{spec.i18n_prefix}.examples")
        if examples:
            lines.append(self.i18n.text(locale, label + "examples"))
            lines.extend(f"    {example}" for example in examples.split("\n") if example)
        return "\n".join(lines)

    def root_help(self, locale: str) -> str:
        label = "dota2tracker.cli_help."
        lines = [self.i18n.text(locale, label + "command", [GROUP])]
        description = self.i18n.t(locale, f"commands.{GROUP}.description")
        if description:
            lines.append(description)
        lines.append(self.i18n.text(locale, label + "subcommands"))
        for spec in COMMANDS:
            summary = self.i18n.t(locale, f"{spec.i18n_prefix}.description") or ""
            lines.append(f"    {spec.legacy_name}  {summary}".rstrip())
        return "\n".join(lines)

    # -------------------------------------------------------------- commands
    async def cmd_help(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        try:
            image = await self.images.help(ctx.locale)
        except RenderError as error:
            self._render_failed(ctx, error)
            yield OutgoingMessage.of(self.i18n.text(ctx.locale, "commands.dota2tracker.help.messages.footer"))
            return
        yield OutgoingMessage.of(image, self.i18n.text(ctx.locale, "commands.dota2tracker.help.messages.footer"))

    async def cmd_subscribe(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        if ctx.chat.is_private:
            return
        if not self.bindings.platform_supported(ctx.chat.platform_type):
            yield self._text(ctx, COMMON + "platform_not_supported", {"platform": ctx.chat.platform_type})
            return
        if await self.bindings.is_subscribed(ctx.chat):
            yield self._text(ctx, ".subscribed")
            return
        await self.bindings.subscribe(ctx.chat)
        yield self._text(ctx, ".subscribe_success")

    async def cmd_unsubscribe(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        if ctx.chat.is_private:
            return
        if await self.bindings.unsubscribe(ctx.chat):
            yield self._text(ctx, ".unsubscribe_success")
        else:
            yield self._text(ctx, ".not_subscribed")

    async def cmd_bind(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        if ctx.chat.is_private:
            return
        steam_id = args[0] if args else None
        nickname = args[1] if len(args) > 1 else None
        if not steam_id or not STEAM_ID_PATTERN.match(steam_id):
            yield self._text(ctx, ".steam_id_invalid")
            return
        if nickname is not None and nickname != "" and not _valid_nickname(nickname):
            yield self._text(ctx, ".nick_name_too_long")
            return
        existing = await self.bindings.get_binding(ctx.chat)
        if existing is not None:
            yield self._text(ctx, ".already_binded", _binding_params(existing))
            return
        verification = await self.players.validate_steam_id(int(steam_id))
        if verification.status != "VALID":
            reason = self.i18n.text(ctx.locale, ctx.message_key(verification.reason.lstrip(".")))
            yield self._text(ctx, ".bind_failed", [reason])
            return
        record = await self.bindings.bind(ctx.chat, int(steam_id), nickname or "")
        if record is None:
            existing = await self.bindings.get_binding(ctx.chat)
            yield self._text(ctx, ".already_binded", _binding_params(existing) if existing else {})
            return
        text = self.i18n.text(
            ctx.locale,
            ctx.message_key("bind_success"),
            {"userId": ctx.chat.user_id, "nickName": nickname or "", "steamId": steam_id},
        )
        if verification.is_anonymous:
            text += "\n" + self.i18n.text(ctx.locale, ctx.message_key("is_anonymous"))
        yield OutgoingMessage.of(text)

    async def cmd_unbind(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        if ctx.chat.is_private:
            return
        if await self.bindings.get_binding(ctx.chat) is not None:
            await self.bindings.unbind(ctx.chat)
            yield self._text(ctx, ".unbind_success")
        else:
            yield self._text(ctx, ".not_binded")

    async def cmd_rename(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        if ctx.chat.is_private:
            return
        player = await self.bindings.get_binding(ctx.chat)
        if player is None:
            yield self._text(ctx, ".not_binded")
            return
        nickname = args[0] if args else ""
        if not nickname:
            # Target fix: the original referenced the misspelled key ".emtpy_input".
            yield self._text(ctx, ".empty_input")
            return
        if not _valid_nickname(nickname):
            yield self._text(ctx, ".nick_name_too_long")
            return
        if nickname == player.nickname:
            yield self._text(ctx, ".nick_name_same")
            return
        await self.bindings.rename(player, nickname)
        yield self._text(ctx, ".rename_success", {"nick_name": nickname})

    async def cmd_query_members(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        if ctx.chat.is_private:
            return
        try:
            bindings = await self.bindings.players_in_channel(ctx.chat)
            members = await self.players.guild_members(bindings)
            if not members:
                yield self._text(ctx, ".no_members")
                return
            yield OutgoingMessage.of(await self.images.members(members, ctx.locale, self.clock()))
        except Exception as error:
            self.reporter.report(error, "command/query-members")
            yield self._text(ctx, ".query_failed")

    async def cmd_query_match(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        match_id = args[0] if args else None
        try:
            if not match_id:
                yield self._text(ctx, ".empty_input")
                return
            if not STEAM_ID_PATTERN.match(match_id):
                yield self._text(ctx, ".match_id_invalid")
                return
            await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("querying_match")))
            message = await self._query_match(ctx, int(match_id), options)
            await ctx.tips.finish()
            yield message
        except Exception as error:
            await ctx.tips.finish()
            self.reporter.report(error, "command/query-match")
            yield self._text(ctx, ".query_failed")

    async def cmd_query_recent_match(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        try:
            steam_id, failure = await self._resolve_player(ctx, args[0] if args else None)
            if failure is not None:
                yield failure
                return
            await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("querying_match")))
            last = await self.players.last_match(int(steam_id))
            if last.is_anonymous:
                # Target fix: the original checked "!id" first, making this message unreachable.
                await ctx.tips.finish()
                yield self._text(ctx, ".is_anonymous")
                return
            if not last.id:
                await ctx.tips.finish()
                yield self._text(ctx, ".query_failed")
                return
            message = await self._query_match(ctx, last.id, options)
            await ctx.tips.finish()
            yield message
        except Exception as error:
            await ctx.tips.finish()
            self.reporter.report(error, "command/query-recent-match")
            yield self._text(ctx, ".query_failed")

    async def _query_match(self, ctx: _Context, match_id: int, options: dict[str, Any]) -> OutgoingMessage:
        """``handleQueryMatchCommand``."""
        template = options.get("template") or None
        if template is not None and template not in MATCH_TEMPLATES:
            raise ValueError(f"unknown match template {template!r}")
        result = await self.matches.get_match_result(
            match_id, wait_for_parse=bool(options.get("parse")), allow_fallback=self.config.enable_opendota_fallback
        )
        if result.status == "PENDING":
            subscriber = Subscriber(
                source="COMMAND",
                type="PRIVATE" if ctx.chat.is_private else "CHANNEL",
                umo=ctx.chat.umo,
                platform=ctx.chat.platform_id,
                channel_id=ctx.chat.channel_id,
                language_tag=ctx.locale,
                user_id=ctx.chat.user_id,
                template_name=template,
            )
            await self.pending.add(match_id, [subscriber])
            return OutgoingMessage.of(
                self.i18n.text(ctx.locale, "commands.dota2tracker.query-match.messages.waiting_for_parse")
            )
        if result.status == "NOT_FOUND":
            return OutgoingMessage.of(self.i18n.text(ctx.locale, ctx.message_key("query_failed")))
        match = await self.matches.generate_match_data(result.match_data or {})
        text = self.messages.match_message(ctx.locale, match, [])
        image = await self.images.match(match, ctx.locale, template or self.config.template_match)
        return OutgoingMessage.of(text, image)

    async def cmd_query_player(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        value = args[0] if args else None
        if ctx.chat.is_private and not value:
            yield self._text(ctx, COMMON + "user_not_in_group")
            return
        try:
            steam_id, failure = await self._resolve_player(ctx, value)
            if failure is not None:
                yield failure
                return
            await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("querying_player")))
            hero_id = self.i18n.find_hero_id(options.get("hero"))
            player = await self.players.formatted_player_data(int(steam_id), hero_id, ctx.locale)
            image = await self.images.player(player, ctx.locale, self.config.template_player)
            await ctx.tips.finish()
            yield OutgoingMessage.of(self.messages.player_message(int(steam_id)), image)
        except Exception as error:
            await ctx.tips.finish()
            self.reporter.report(error, "command/query-player")
            yield self._text(ctx, ".query_failed")

    async def cmd_query_hero(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        value = args[0] if args else None
        random = bool(options.get("random"))
        if not value and not random:
            # Target fix: the original stayed silent; the existing "empty_input" text is used instead.
            yield self._text(ctx, ".empty_input")
            return
        try:
            await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("querying_hero")))
            hero_id = self.heroes.resolve_hero_id(value, random=random)
            if not hero_id:
                await ctx.tips.finish()
                yield self._text(ctx, ".not_found")
                return

            async def on_stale() -> None:
                await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("fetching_patch_notes")))

            hero = await self.heroes.hero_details(hero_id, ctx.locale, on_stale)
            image = await self.images.hero(hero, ctx.locale, self.config.template_hero)
            await ctx.tips.finish()
            yield OutgoingMessage.of(self.messages.hero_message(hero), image)
        except Exception as error:
            await ctx.tips.finish()
            self.reporter.report(error, "command/query-hero")
            yield self._text(ctx, ".query_failed")

    async def cmd_query_item(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        keyword = args[0] if args else None
        show_list = self.config.show_item_list_at_too_much_items
        if not keyword and not show_list:
            yield self._text(ctx, ".empty_input", {"show": show_list})
            return
        try:
            await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("querying_item")))

            async def on_cache_miss() -> None:
                await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("cache_building")))

            item_list = await self.items.item_list(ctx.locale, on_cache_miss)
            if not keyword:
                image = await self.images.itemlist(item_list, ctx.locale)
                await ctx.tips.finish()
                yield OutgoingMessage.of(image)
                return
            matched = self.items.search(item_list, keyword, ctx.locale)
            if not matched:
                await ctx.tips.finish()
                yield self._text(ctx, ".not_found")
                return
            limit = self.config.max_send_item_count
            if len(matched) > limit:
                await ctx.tips.finish()
                # Target fix: "show" is passed so the message mentions the list image.
                yield self._text(ctx, ".too_many_items", {"count": len(matched), "max": limit, "show": show_list})
                if show_list:
                    yield OutgoingMessage.of(await self.images.itemlist(matched, ctx.locale))
                return
            await ctx.tips.finish()
            yield self._text(ctx, ".finded_items", {"items": matched})
        except Exception as error:
            await ctx.tips.finish()
            self.reporter.report(error, "command/query-item")
            yield self._text(ctx, ".query_list_failed")
            return
        for basic in matched:
            try:
                details = await self.items.item_details(int(basic["id"]), ctx.locale)
                yield OutgoingMessage.of(await self.images.item({**details, **basic}, ctx.locale))
            except Exception as error:
                self.reporter.report(error, "command/query-item")
                # Target fix: the locale uses "{0}"; the original passed a named parameter.
                yield self._text(ctx, ".query_item_failed", [basic.get("name_loc")])

    async def cmd_hero_of_the_day(self, ctx: _Context, args: list[str], options: dict[str, Any]) -> Reply:
        try:
            await ctx.tips.send(self.i18n.text(ctx.locale, ctx.message_key("querying")))
            steam_id, failure = await self._resolve_player(ctx, args[0] if args else None)
            if failure is not None:
                await ctx.tips.finish()  # Target fix: the original left the tip un-recalled here.
                yield failure
                return
            days = _clamp(options.get("days"), 1, 180, 30)
            personal, meta = await self.recommendations.recommend(int(steam_id), days)
            image = await self.images.hero_of_the_day(personal, meta, ctx.locale)
            await ctx.tips.finish()
            yield OutgoingMessage.of(image)
        except Exception as error:
            await ctx.tips.finish()
            self.reporter.report(error, "command/hero-of-the-day")
            yield self._text(ctx, ".query_failed")


def _binding_params(player) -> dict[str, Any]:
    return {"userId": player.user_id, "nickName": player.nickname, "steamId": player.steam_id}
