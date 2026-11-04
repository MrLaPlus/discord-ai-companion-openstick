import itertools
import random
import unittest
from types import SimpleNamespace

import discord

from bot.policy import audience_can_read

VIEW = discord.Permissions.VALID_FLAGS["view_channel"]
HISTORY = discord.Permissions.VALID_FLAGS["read_message_history"]


class Guild:
    id = 1
    owner_id = 99999

    def __init__(self, permissions):
        self.state = SimpleNamespace()
        self.state.store_user = lambda data: discord.User(state=self.state, data=data)
        self._roles = {
            index: discord.Role(
                guild=self,
                state=self.state,
                data={"id": str(index), "name": str(index), "permissions": str(value), "position": index},
            )
            for index, value in enumerate(permissions, 1)
        }

    @property
    def roles(self):
        return list(self._roles.values())

    @property
    def default_role(self):
        return self._roles[1]

    def get_role(self, role):
        return self._roles.get(role)

    def get_member(self, user):
        return None  # Intentionally exercise uncached per-member overwrites.

    def channel(self, cid, overwrites):
        return discord.TextChannel(
            state=self.state,
            guild=self,
            data={
                "id": str(cid),
                "name": "room",
                "type": 0,
                "position": 0,
                "permission_overwrites": overwrites,
            },
        )

    def member(self, roles, uid=100):
        return discord.Member(
            data={
                "user": {"id": str(uid), "username": "test", "discriminator": "0", "avatar": None},
                "roles": [str(r) for r in roles],
                "flags": 0,
            },
            guild=self,
            state=self.state,
        )


def overwrite(target, allow=0, deny=0, member=False):
    return {"id": str(target), "type": 1 if member else 0, "allow": str(allow), "deny": str(deny)}


class AudienceTests(unittest.TestCase):
    def test_same_restricted_audience_is_allowed(self):
        guild = Guild([HISTORY, VIEW])
        acl = [overwrite(1, deny=VIEW), overwrite(2, allow=VIEW)]
        source, destination = guild.channel(20, acl), guild.channel(21, acl)
        self.assertTrue(audience_can_read(source, destination))

    def test_private_source_cannot_flow_to_broader_destination(self):
        guild = Guild([HISTORY, VIEW])
        source = guild.channel(20, [overwrite(1, deny=VIEW), overwrite(2, allow=VIEW)])
        destination = guild.channel(21, [overwrite(1, allow=VIEW)])
        self.assertFalse(audience_can_read(source, destination))

    def test_uncached_member_denial_is_not_missed(self):
        guild = Guild([VIEW | HISTORY])
        source = guild.channel(20, [overwrite(123, deny=HISTORY, member=True)])
        self.assertFalse(audience_can_read(source, guild.channel(21, [])))

    def test_destination_view_without_source_history_is_rejected(self):
        guild = Guild([VIEW])
        self.assertFalse(audience_can_read(guild.channel(20, []), guild.channel(21, [])))

    def test_role_allow_beats_other_role_deny_like_discord(self):
        guild = Guild([VIEW | HISTORY, 0, 0])
        source = guild.channel(20, [overwrite(2, deny=VIEW), overwrite(3, allow=VIEW)])
        destination = guild.channel(21, [overwrite(1, deny=VIEW), overwrite(3, allow=VIEW)])
        self.assertTrue(audience_can_read(source, destination))

    def test_state_proof_matches_sdk_for_all_member_role_combinations(self):
        rng = random.Random(42)
        values = [0, VIEW, HISTORY, VIEW | HISTORY]
        for case in range(40):
            guild = Guild([rng.choice(values) for _ in range(4)])
            channels = []
            for cid in (20, 21):
                rules = []
                for rid in range(1, 5):
                    allow, deny = rng.choice(values), rng.choice(values)
                    rules.append(overwrite(rid, allow, deny & ~allow))
                for uid in (123, 124):
                    allow, deny = rng.choice(values), rng.choice(values)
                    rules.append(overwrite(uid, allow, deny & ~allow, member=True))
                channels.append(guild.channel(cid, rules))
            source, destination = channels
            expected = True
            for assigned in itertools.product((False, True), repeat=3):
                for uid in (100, 123, 124):
                    member = guild.member([i + 2 for i, has in enumerate(assigned) if has], uid)
                    sp, dp = source.permissions_for(member), destination.permissions_for(member)
                    if dp.view_channel and not (sp.view_channel and sp.read_message_history):
                        expected = False
            with self.subTest(case=case):
                self.assertEqual(audience_can_read(source, destination), expected)
