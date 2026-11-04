import unittest
from unittest.mock import AsyncMock

from bot.football import browser_score_page, football_query, football_request, host_rank
from bot.web_tools import ToolError, WebPage, explicit_query, lookup_candidate
from tests import test_web


class FootballUnitTests(unittest.TestCase):
    def test_fixture_questions_enter_public_lookup_without_latest_keyword(self):
        for question in [
            "แมนยูแข่งวันไหน",
            "ตารางแข่งอาร์เซนอล",
            "โปรแกรมแมนซิตี้",
            "Fleetwood Town fixtures",
            "ตารางคะแนนพรีเมียร์ลีก",
        ]:
            self.assertTrue(football_request(question), question)
            self.assertTrue(lookup_candidate(question), question)
        for question in [
            "แมนยูเล่นห่วยว่ะ",
            "แมนยูแข่งแพ้อีกแล้ว 555",
            "Man City play like trash",
            "สกอร์เกมแมว",
            "Arsenal code editor",
            "เล่นบอลกันไหม",
        ]:
            self.assertFalse(football_request(question), question)

    def test_source_hints_preserve_date_category_and_opponent(self):
        question = "ตารางแข่งอาร์เซนอลหญิงกับเชลซี 2026-11-01"
        query = football_query(question, explicit_query(question))
        for word in [
            "Arsenal",
            "หญิง",
            "เชลซี",
            "2026-11-01",
            "fixtures",
            "site:arsenal.com",
            "site:livescore.com",
        ]:
            self.assertIn(word, query)
        self.assertLessEqual(len(query), 400)
        long = "ตารางแข่งแมนยู " + "x" * 360 + " 2020-01-01"
        result = football_query(long, long)
        self.assertIn("2020-01-01", result)
        self.assertLessEqual(len(result), 400)

    def test_exact_public_hosts_only_and_unknown_teams_are_not_mapped_to_united(self):
        self.assertEqual(host_rank("https://www.arsenal.com/fixtures", "ตารางแข่งน่อล"), 0)
        self.assertEqual(host_rank("https://arsenal.com.evil.example/a", "ตารางแข่งน่อล"), 100)
        self.assertFalse(browser_score_page("https://evilsofascore.com/a"))
        self.assertTrue(browser_score_page("https://www.sofascore.com/a"))
        self.assertFalse(browser_score_page("https://www.mancity.com/fixtures/mens"))
        query = football_query("ผลบอลลิเวอร์พูล", "ผลบอลลิเวอร์พูล")
        self.assertNotIn("Manchester United", query)
        self.assertIn("ลิเวอร์พูล", query)
        self.assertIn("site:premierleague.com", query)


class FootballAppTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_web.WebAppTests.asyncSetUp
    asyncTearDown = test_web.WebAppTests.asyncTearDown
    job = test_web.WebAppTests.job
    enable = test_web.WebAppTests.enable

    async def test_fixtures_prioritize_club_then_browser_with_no_planner_or_history(self):
        self.enable()
        self.message.content = "ค้นตารางแข่งแมนยู"
        self.bot.history.hydrate = AsyncMock()
        self.bot.web.call.side_effect = [
            [
                WebPage("Other", "https://example.com", "unknown", "now"),
                WebPage("United", "https://www.manutd.com/en/matches", "Fixtures", "now"),
            ],
            [
                WebPage(
                    "United",
                    "https://www.manutd.com/en/matches",
                    "scheduled 1 November 2026 15:00 UTC",
                    "now",
                )
            ],
        ]
        await self.bot.process(self.job())
        calls = self.bot.web.call.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], "search")
        self.assertIn("site:manutd.com", calls[0].args[1]["query"])
        self.bot.history.hydrate.assert_not_awaited()
        prompt = self.bot.provider.chat.call_args.args[0][0]["content"]
        self.assertIn("Asia/Bangkok", prompt)
        self.assertIn("not continuous updates", prompt)
        self.assertIn("do not verify a current score", prompt)

    async def test_private_football_followup_requires_confirmation_without_egress(self):
        self.enable()
        self.message.content = "ค้นผลบอลทีมที่คุยในห้องเมื่อกี้"
        await self.bot.process(self.job())
        self.bot.web.call.assert_not_awaited()
        self.bot.provider.chat.assert_not_awaited()

    async def test_browser_failure_does_not_add_third_call_or_claim_read_content(self):
        self.enable()
        self.bot.web.call.side_effect = [
            [
                WebPage(
                    "United", "https://www.manutd.com/en/matches", "Search snippet: scheduled match", "now"
                )
            ],
            ToolError("browser unavailable"),
        ]
        await self.bot.process(self.job())
        self.assertEqual(self.bot.web.call.await_count, 1)
        messages = str(self.bot.provider.chat.call_args)
        self.assertIn("Search snippet", messages)
        self.assertIn("Never call snippets", messages)
        self.assertNotIn("browser unavailable", messages)

    async def test_revision_change_blocks_second_call(self):
        self.enable()

        async def changed(*args, **kwargs):
            self.bot.invalidate()
            return [WebPage("United", "https://www.manutd.com/en/matches", "x", "now")]

        self.bot.web.call.side_effect = changed
        await self.bot.process(self.job())
        self.assertEqual(self.bot.web.call.await_count, 1)
        self.message.reply.assert_not_awaited()
