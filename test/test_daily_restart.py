import asyncio
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch


os.environ.setdefault("DISCORD_TOKEN", "test-token")

import main


class DailyRestartScheduleTests(unittest.TestCase):
    def test_next_restart_uses_the_next_four_am_in_korea(self) -> None:
        for current, expected in (
            ("2026-10-14T03:59:59+09:00", "2026-10-14T04:00:00+09:00"),
            ("2026-10-14T04:00:00+09:00", "2026-10-15T04:00:00+09:00"),
            ("2026-10-14T04:00:01+09:00", "2026-10-15T04:00:00+09:00"),
            ("2026-10-31T23:59:59+09:00", "2026-11-01T04:00:00+09:00"),
            ("2026-12-31T23:59:59+09:00", "2027-01-01T04:00:00+09:00"),
            ("2026-10-13T18:59:59+00:00", "2026-10-14T04:00:00+09:00"),
            ("2026-10-13T19:00:00+00:00", "2026-10-15T04:00:00+09:00"),
        ):
            with self.subTest(current=current):
                result = main.get_next_daily_restart(datetime.fromisoformat(current))

                self.assertEqual(result, datetime.fromisoformat(expected))
                self.assertIs(result.tzinfo, main.KOREA_TIMEZONE)


class DailyRestartTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_timer_rechecks_wall_clock_and_handles_clock_jumps(self) -> None:
        for label, timestamps, waits in (
            (
                "normal countdown",
                ("03:58:30", "03:58:30", "03:59:30", "04:00:00"),
                (60, 30),
            ),
            (
                "forward clock jump",
                ("03:58:00", "03:58:00", "04:30:00"),
                (60,),
            ),
            (
                "backward clock jump",
                ("03:59:30", "03:59:30", "03:58:00", "03:59:55", "04:00:00"),
                (30, 60, 5),
            ),
        ):
            with self.subTest(label=label):
                bot = SimpleNamespace(is_closed=Mock(return_value=False), restart_requested=False)
                times = [
                    datetime.fromisoformat(f"2026-10-14T{value}+09:00")
                    for value in timestamps
                ]
                with (
                    patch.object(main, "datetime") as clock,
                    patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep,
                ):
                    clock.now.side_effect = times

                    await main.run_daily_restart(bot)

                self.assertTrue(bot.restart_requested)
                self.assertEqual(sleep.await_args_list, [call(value) for value in waits])
                self.assertEqual(
                    clock.now.call_args_list,
                    [call(main.KOREA_TIMEZONE)] * len(times),
                )

    async def test_closed_bot_does_not_request_restart(self) -> None:
        bot = SimpleNamespace(is_closed=Mock(return_value=True), restart_requested=False)
        with patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await main.run_daily_restart(bot)

        self.assertFalse(bot.restart_requested)
        sleep.assert_not_awaited()

    async def test_cancelled_timer_does_not_request_restart(self) -> None:
        bot = SimpleNamespace(is_closed=Mock(return_value=False), restart_requested=False)
        with patch.object(main.asyncio, "sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await main.run_daily_restart(bot)

        self.assertFalse(bot.restart_requested)


class LunaClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_interrupts_stuck_discord_connection_before_ready(self) -> None:
        bot = main.LunaClient(intents=main.discord.Intents.none())
        connection_started = asyncio.Event()
        connection_stopped = asyncio.Event()

        async def connection(instance, token, *, reconnect=True):
            self.assertIs(instance, bot)
            self.assertEqual(token, "test-token")
            self.assertFalse(reconnect)
            connection_started.set()
            try:
                await asyncio.Future()
            finally:
                connection_stopped.set()

        async def timer(instance):
            await connection_started.wait()
            instance.restart_requested = True

        with (
            patch.object(main.discord.Client, "start", new=connection),
            patch.object(main, "run_daily_restart", new=timer),
        ):
            await asyncio.wait_for(bot.start("test-token", reconnect=False), timeout=1)

        self.assertFalse(bot.is_ready())
        self.assertTrue(bot.restart_requested)
        self.assertTrue(connection_stopped.is_set())

    async def test_normal_connection_exit_cancels_daily_timer(self) -> None:
        bot = main.LunaClient(intents=main.discord.Intents.none())
        timer_started = asyncio.Event()
        timer_stopped = asyncio.Event()

        async def connection(*args, **kwargs):
            await timer_started.wait()

        async def timer(instance):
            timer_started.set()
            try:
                await asyncio.Future()
            finally:
                timer_stopped.set()

        with (
            patch.object(main.discord.Client, "start", new=connection),
            patch.object(main, "run_daily_restart", new=timer),
        ):
            await asyncio.wait_for(bot.start("test-token"), timeout=1)

        self.assertFalse(bot.restart_requested)
        self.assertTrue(timer_stopped.is_set())

    async def test_connection_and_timer_failures_propagate_and_cancel_peer(self) -> None:
        for failing_task in ("connection", "timer"):
            with self.subTest(failing_task=failing_task):
                bot = main.LunaClient(intents=main.discord.Intents.none())
                peer_started = asyncio.Event()
                peer_stopped = asyncio.Event()

                async def run_task(name):
                    if name == failing_task:
                        await peer_started.wait()
                        raise RuntimeError(f"{name} failed")
                    peer_started.set()
                    try:
                        await asyncio.Future()
                    finally:
                        peer_stopped.set()

                async def connection(*args, **kwargs):
                    await run_task("connection")

                async def timer(instance):
                    await run_task("timer")

                with (
                    patch.object(main.discord.Client, "start", new=connection),
                    patch.object(main, "run_daily_restart", new=timer),
                ):
                    with self.assertRaisesRegex(RuntimeError, f"{failing_task} failed"):
                        await asyncio.wait_for(bot.start("test-token"), timeout=1)

                self.assertFalse(bot.restart_requested)
                self.assertTrue(peer_stopped.is_set())

    async def test_external_cancellation_cancels_both_tasks_and_prevents_restart(self) -> None:
        bot = main.LunaClient(intents=main.discord.Intents.none())
        started = {name: asyncio.Event() for name in ("connection", "timer")}
        stopped = {name: asyncio.Event() for name in started}

        async def run_task(name):
            started[name].set()
            try:
                await asyncio.Future()
            finally:
                stopped[name].set()

        async def connection(*args, **kwargs):
            await run_task("connection")

        async def timer(instance):
            instance.restart_requested = True
            await run_task("timer")

        with (
            patch.object(main.discord.Client, "start", new=connection),
            patch.object(main, "run_daily_restart", new=timer),
        ):
            task = asyncio.create_task(bot.start("test-token"))
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())),
                timeout=1,
            )
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertFalse(bot.restart_requested)
        self.assertTrue(all(event.is_set() for event in stopped.values()))


if __name__ == "__main__":
    unittest.main()
