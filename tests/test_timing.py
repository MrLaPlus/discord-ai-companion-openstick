import asyncio
import json
import unittest
from bot import timing
from tests import test_app as fixtures


class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_contexts_remain_separate_and_errors_are_timed(self):
        @timing.timed("step")
        async def sample(fail=False):
            await asyncio.sleep(0)
            if fail:
                raise ValueError("private message must not be recorded")

        async def run(fail):
            trace = {}
            token = timing.current.set(trace)
            try:
                try:
                    await sample(fail)
                except ValueError:
                    pass
                return trace
            finally:
                timing.current.reset(token)

        traces = await asyncio.gather(run(True), run(False))
        self.assertEqual([set(t) for t in traces], [{"step"}, {"step"}])
        self.assertTrue(all(t["step"] >= 0 for t in traces))


class WorkerTimingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AppTests.asyncSetUp
    asyncTearDown = fixtures.AppTests.asyncTearDown
    async def test_worker_logs_only_bounded_duration_fields(self):
        self.message.content = "SECRET_PRIVATE_CONTENT"
        self.bot.store.put_message(1, 10, 20, 30, self.message.content, self.message.created_at.timestamp())
        with self.assertLogs("bot.app", level="INFO") as logs:
            await self.bot.enqueue(self.message, "direct", self.message.content)
            worker = asyncio.create_task(self.bot.worker())
            try:
                await asyncio.wait_for(self.bot.queue.join(), 5)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
        entries = [line.split("job_durations_seconds ", 1)[1] for line in logs.output
                   if "job_durations_seconds " in line]
        self.assertEqual(len(entries), 1)
        data = json.loads(entries[0])
        self.assertTrue({"prequeue", "queue", "channel_lock", "bot_total", "context_build", "discord_send"} <= set(data))
        self.assertTrue(all(isinstance(v, (float, int)) and v >= 0 for v in data.values()))
        self.assertNotIn("SECRET_PRIVATE_CONTENT", entries[0])
        self.assertNotIn("http", entries[0])
