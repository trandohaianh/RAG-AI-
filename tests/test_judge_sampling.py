import asyncio

import chat.router as chat_router


def _scheduled_calls(monkeypatch, rate: float, attempts: int = 50) -> int:
    calls = []

    async def fake_judge(question, context, answer):
        calls.append(question)
        return None

    monkeypatch.setattr(chat_router, "judge_answer", fake_judge)
    monkeypatch.setattr(chat_router, "JUDGE_SAMPLE_RATE", rate)

    async def run():
        for i in range(attempts):
            chat_router._schedule_eval(i, "q", "ctx", "a")
        await asyncio.gather(*list(chat_router._background_tasks))

    asyncio.run(run())
    return len(calls)


def test_judge_skipped_when_rate_is_zero(monkeypatch):
    assert _scheduled_calls(monkeypatch, 0.0) == 0


def test_judge_runs_every_answer_when_rate_is_one(monkeypatch):
    assert _scheduled_calls(monkeypatch, 1.0) == 50
