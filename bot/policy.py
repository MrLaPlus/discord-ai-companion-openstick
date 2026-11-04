from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import discord


def read_access(channel, member) -> bool:
    permissions = channel.permissions_for(member)
    return bool(permissions.view_channel and permissions.read_message_history)


def public_history(channel) -> bool:
    """Conservative audience proof: public history with no role/member read denials."""
    if not read_access(channel, channel.guild.default_role):
        return False
    for overwrite in channel.overwrites.values():
        if overwrite.view_channel is False or overwrite.read_message_history is False:
            return False
    return True


VIEW = 1
HISTORY = 2


def read_bits(permissions) -> int:
    return (VIEW if permissions.view_channel else 0) | (HISTORY if permissions.read_message_history else 0)


def acl(channel) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    roles, members = {}, {}
    # The public property preserves uncached users as type-aware discord.Object instances.
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        is_role = isinstance(target, discord.Role) or (
            isinstance(target, discord.Object) and target.type is discord.Role
        )
        (roles if is_role else members)[target.id] = (read_bits(allow), read_bits(deny))
    return roles, members


def audience_can_read(source, destination) -> bool:
    """Prove: every possible destination viewer can view AND read source history.

    Enumerate reachable permission states, not cached guild members or 2**role combinations.
    Two relevant permission bits give at most 4**5 = 1024 states. This also covers future
    role combinations and uncached member-specific overrides, failing closed on unknown data.
    """
    if source.guild.id != destination.guild.id:
        return False
    roles = getattr(source.guild, "roles", None)
    if not isinstance(roles, list) or not roles:
        # Legacy test doubles / incomplete snapshots cannot prove a restricted audience.
        return public_history(source) and public_history(destination)
    try:
        src_roles, src_members = acl(source)
        dst_roles, dst_members = acl(destination)
        base_role = next(r for r in roles if r.id == source.guild.id)
        if base_role.permissions.administrator:
            return True
        src_everyone = src_roles.get(source.guild.id, (0, 0))
        dst_everyone = dst_roles.get(source.guild.id, (0, 0))
        states = {(read_bits(base_role.permissions), 0, 0, 0, 0)}
        contributions = set()
        for role in roles:
            if role.id == source.guild.id or role.permissions.administrator:
                continue  # Administrator members bypass both ACLs, so cannot be counterexamples.
            sa, sd = src_roles.get(role.id, (0, 0))
            da, dd = dst_roles.get(role.id, (0, 0))
            contributions.add((read_bits(role.permissions), sd, sa, dd, da))
        for contribution in contributions:
            states |= {tuple(a | b for a, b in zip(state, contribution)) for state in states}
        for member in {None, *src_members, *dst_members}:
            for base, sd, sa, dd, da in states:
                src = ((base & ~src_everyone[1]) | src_everyone[0]) & ~sd | sa
                dst = ((base & ~dst_everyone[1]) | dst_everyone[0]) & ~dd | da
                if member in src_members:
                    allow, deny = src_members[member]
                    src = (src & ~deny) | allow
                if member in dst_members:
                    allow, deny = dst_members[member]
                    dst = (dst & ~deny) | allow
                if dst & VIEW and src & (VIEW | HISTORY) != VIEW | HISTORY:
                    return False
        return True
    except (AttributeError, TypeError, ValueError, StopIteration):
        return False


def eligible_channels(
    destination, candidates, settings, requester, bot_member, *, private: bool = False
) -> list[int]:
    if not read_access(destination, requester) or not read_access(destination, bot_member):
        return []
    target = settings(destination.id)
    if not target["readable"]:
        return []
    result = [destination.id]
    if not target["share_group"]:
        return result
    for source in candidates:
        if source.id == destination.id or source.guild.id != destination.guild.id:
            continue
        source_settings = settings(source.id)
        if (
            source_settings["readable"]
            and source_settings["share_group"] == target["share_group"]
            and read_access(source, requester)
            and read_access(source, bot_member)
            and (private or audience_can_read(source, destination))
            and (not source.is_nsfw() or destination.is_nsfw())
        ):
            result.append(source.id)
    return sorted(result)


def quiet(settings: dict, now: float, timezone: str) -> bool:
    start, end = settings["quiet_start"], settings["quiet_end"]
    if start < 0 or end < 0:
        return False
    hour = datetime.fromtimestamp(now, ZoneInfo(timezone)).hour
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end


def trigger(
    settings: dict,
    *,
    mention: bool,
    reply: bool,
    now: float,
    timezone: str,
    interjections: list[float],
    draw=None,
) -> str | None:
    if settings["paused"] or not settings["respond"]:
        return None
    if mention or reply:
        return "direct"
    if settings["bot_room"]:
        return "room"
    if settings["interject"] <= 0 or quiet(settings, now, timezone):
        return None
    if interjections and now - max(interjections) < settings["cooldown"]:
        return None
    if sum(t > now - 3600 for t in interjections) >= settings["hourly"]:
        return None
    if (random.random() if draw is None else draw) < settings["interject"]:
        return "interject"
    return None
