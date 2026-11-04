import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from bot.policy import eligible_channels, trigger
from bot.storage import Store


class Channel:
    def __init__(self, cid, guild, *, public=True, deny=False, nsfw=False, blocked=None):
        self.id, self.guild = cid, guild
        self.public, self.nsfw, self.blocked = public, nsfw, blocked
        self.overwrites = {
            1: SimpleNamespace(view_channel=False if deny else None, read_message_history=None)
        }

    def permissions_for(self, member):
        allowed = (self.public or member != self.guild.default_role) and member != self.blocked
        return SimpleNamespace(view_channel=allowed, read_message_history=allowed)

    def is_nsfw(self):
        return self.nsfw


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.guild = SimpleNamespace(id=10, default_role="everyone")
        self.destination = Channel(20, self.guild)
        self.source = Channel(21, self.guild)
        for cid in (20, 21):
            self.store.set_channel(10, cid, readable=1, respond=1, share_group="friends")

    def tearDown(self):
        self.store.close()

    def allowed(self, source=None):
        return eligible_channels(
            self.destination,
            [self.destination, source or self.source],
            lambda cid: self.store.channel(10, cid),
            "user",
            "bot",
        )

    def test_public_explicit_group_allows_cross_channel(self):
        self.assertEqual(self.allowed(), [20, 21])

    def test_no_group_disables_cross_channel(self):
        self.store.set_channel(10, 20, share_group="")
        self.assertEqual(self.allowed(), [20])

    def test_private_source_never_leaks_even_to_admin_requester(self):
        self.assertEqual(self.allowed(Channel(21, self.guild, public=False)), [20])

    def test_member_or_role_deny_blocks_cross_channel(self):
        self.assertEqual(self.allowed(Channel(21, self.guild, deny=True)), [20])

    def test_private_destination_stays_local(self):
        self.destination.public = False
        self.assertEqual(self.allowed(), [20])

    def test_requester_loses_access(self):
        self.destination.blocked = "user"
        self.assertEqual(self.allowed(), [])

    def test_bot_loses_access_to_source(self):
        self.assertEqual(self.allowed(Channel(21, self.guild, blocked="bot")), [20])

    def test_nsfw_cannot_flow_to_general_channel(self):
        self.assertEqual(self.allowed(Channel(21, self.guild, nsfw=True)), [20])

    def test_private_answer_can_use_restricted_source_for_authorized_requester(self):
        source = Channel(21, self.guild, public=False)
        allowed = eligible_channels(
            self.destination, [source], lambda cid: self.store.channel(10, cid), "user", "bot", private=True
        )
        self.assertEqual(allowed, [20, 21])
        source.blocked = "user"
        self.assertEqual(
            eligible_channels(
                self.destination,
                [source],
                lambda cid: self.store.channel(10, cid),
                "user",
                "bot",
                private=True,
            ),
            [20],
        )

    def test_read_allowlist_and_guild_boundary(self):
        self.store.set_channel(10, 21, readable=0)
        self.assertEqual(self.allowed(), [20])
        other = SimpleNamespace(id=11, default_role="everyone")
        self.assertEqual(self.allowed(Channel(21, other)), [20])

    def decide(self, settings=None, **kwargs):
        defaults = dict(
            mention=False, reply=False, now=10000, timezone="Asia/Bangkok", interjections=[], draw=0
        )
        defaults.update(kwargs)
        return trigger(settings or self.store.channel(10, 20), **defaults)

    def test_default_mentions_and_replies_only(self):
        self.assertIsNone(self.decide())
        self.assertEqual(self.decide(mention=True), "direct")
        self.assertEqual(self.decide(reply=True), "direct")

    def test_bot_room_and_pause(self):
        self.store.set_channel(10, 20, bot_room=1)
        self.assertEqual(self.decide(), "room")
        self.store.set_channel(10, 20, paused=1)
        self.assertIsNone(self.decide(mention=True))

    def test_interjection_cooldown_and_hourly_cap(self):
        self.store.set_channel(10, 20, interject=1, cooldown=300, hourly=2)
        self.assertEqual(self.decide(), "interject")
        self.assertIsNone(self.decide(interjections=[9900]))
        self.assertIsNone(self.decide(interjections=[9100, 9500]))

    def test_quiet_hours_wrap_midnight(self):
        self.store.set_channel(10, 20, interject=1, quiet_start=22, quiet_end=7)
        now = datetime(2026, 10, 27, 17, tzinfo=timezone.utc).timestamp()  # midnight Bangkok
        self.assertIsNone(self.decide(now=now))
        self.assertEqual(self.decide(now=now, mention=True), "direct")
