import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.board_status import _cpu, _cpu_percent, _memory, _snapshot, _temperatures, board_summary
from bot.commands import register
from tests import test_app as fixtures


class BoardMetricTests(unittest.TestCase):
    def test_cpu_interval_excludes_guest_and_handles_bad_samples(self):
        with tempfile.TemporaryDirectory() as d:
            stat = Path(d) / "stat"
            stat.write_text("cpu 100 10 20 300 10 5 5 0 90 8\n")
            first = _cpu(stat)
            stat.write_text("cpu 120 10 30 360 20 5 5 0 100 9\n")
            second = _cpu(stat)
            self.assertEqual(first, (450, 310))
            self.assertEqual(_cpu_percent(first, second), 30.0)
            self.assertIsNone(_cpu_percent(first, first))
            stat.write_text("cpu bad data\n")
            self.assertIsNone(_cpu(stat))

    def test_memory_calculates_available_and_swap(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "meminfo"
            path.write_text("MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 500 kB\nSwapFree: 300 kB\n")
            result = _memory(path)
            self.assertEqual(result["ram"], {"total": 1024000, "used": 614400, "available": 409600})
            self.assertEqual(result["swap"], {"total": 512000, "used": 204800})
            path.write_text("MemTotal: invalid kB\nMemAvailable: 400 kB\n")
            self.assertEqual(_memory(path), {})

    def test_sensors_label_cpu_gpu_and_skip_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for index, name, temp in (
                (0, "cpu0-1-thermal", "41000"),
                (1, "gpu-thermal", "38000"),
                (2, "bad\\nname", "999999"),
            ):
                folder = root / f"thermal_zone{index}"
                folder.mkdir()
                (folder / "type").write_text(name)
                (folder / "temp").write_text(temp)
            self.assertEqual(_temperatures(root), {"cpu0-1-thermal": 41.0, "gpu-thermal": 38.0})

    def test_missing_sources_are_safe_and_display_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = _snapshot(root / "no-proc", root / "no-sys", root / "no-disk")
            text = board_summary(result)
            self.assertIn("RAM: ไม่พร้อมใช้งาน", text)
            self.assertIn("อุณหภูมิ: ไม่พร้อมใช้งาน", text)
            self.assertIn("เวลาเปิดเครื่อง: ไม่พร้อมใช้งาน", text)


class BoardAssessmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_fallback_only_reports_process_evidence(self):
        from bot.board_status import _service_state
        proc = SimpleNamespace(communicate=AsyncMock(return_value=(b'', b'')))
        with patch('bot.board_status.asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)), patch('bot.board_status._read', return_value='42\n'):
            self.assertIn('มีโปรเซส', await _service_state('discord-usb-search.service'))
        with patch('bot.board_status.asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)), patch('bot.board_status._read', return_value=None):
            self.assertEqual(await _service_state('discord-usb-search.service'), 'ไม่ทราบ')

    async def test_warnings_and_unknown_services_reflect_measurements(self):
        from bot.board_status import board_assessment, is_board_question
        data = {'disk': {'total': 100, 'used': 90, 'available': 10}, 'ram': {'total': 100, 'used': 99, 'available': 1}, 'temperatures_c': {'cpu': 81}}
        with patch('bot.board_status.board_status', new=AsyncMock(return_value=data)), patch('bot.board_status._service_state', new=AsyncMock(side_effect=['ทำงาน', 'ไม่ทราบ'])):
            result = await board_assessment()
        self.assertIn('85%', result)
        self.assertIn('80°C', result)
        self.assertIn('ไม่ทราบ', result)
        self.assertTrue(is_board_question('บอร์ดเป็นไงบ้าง'))
        self.assertFalse(is_board_question('ช่วยอธิบายว่าบอร์ดเป็นไงบ้างในบทความ'))


class StatusCommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AppTests.asyncSetUp
    asyncTearDown = fixtures.AppTests.asyncTearDown

    def interaction(self):
        response = SimpleNamespace(defer=AsyncMock(), is_done=lambda: True)
        return SimpleNamespace(
            response=response, followup=SimpleNamespace(send=AsyncMock()), guild=self.guild
        )

    async def test_status_admin_only_and_keeps_existing_report(self):
        register(self.bot)
        cmd = self.bot.tree.get_command("status")
        self.assertTrue(cmd.default_permissions.manage_guild)
        interaction = self.interaction()
        with (
            patch("bot.panel.guard", new=AsyncMock(side_effect=discord.app_commands.CheckFailure("denied"))),
            patch("bot.board_status.board_status", new_callable=AsyncMock) as measure,
        ):
            with self.assertRaises(discord.app_commands.CheckFailure):
                await cmd.callback(interaction)
            measure.assert_not_awaited()
        self.bot.provider.last_status = "ok"
        interaction = self.interaction()
        with (
            patch("bot.panel.guard", new=AsyncMock()),
            patch("bot.board_status.board_status", new=AsyncMock(return_value={"cpu_percent": 12.5})),
        ):
            await cmd.callback(interaction)
        response = interaction.followup.send.call_args
        self.assertIn("CPU: 12.5%", response.args[0])
        self.assertIn('"discord_ready"', response.args[0])
        self.assertTrue(response.kwargs["ephemeral"])
