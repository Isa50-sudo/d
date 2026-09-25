"""ToolManager: Validierung, Berechtigung, Bestätigung, echte Ausführung."""
from __future__ import annotations

import asyncio

from jarvis.web import sources


async def test_unknown_tool(services):
    result = await services.tools.execute("rm_rf_everything", {})
    assert result["status"] == "error"


async def test_invalid_arguments(services):
    result = await services.tools.execute("list_processes", {"limit": 999})
    assert result["status"] == "error"
    assert "limit" in result["message"]


async def test_real_system_values(services):
    result = await services.tools.execute("get_memory_usage", {})
    assert result["status"] == "ok"
    assert 0 < result["result"]["total_gb"]
    result = await services.tools.execute("get_current_time", {"timezone": "Europe/Vienna"})
    assert result["status"] == "ok" and "Vienna" in result["result"]["timezone"]


async def test_read_only_blocks_write(services):
    await services.settings.update({"permissions": {"access_level": "READ_ONLY"}})
    result = await services.tools.execute("open_url", {"url": "google"})
    assert result["status"] == "denied"
    await services.settings.update({"permissions": {"access_level": "LIMITED"}})


async def test_file_create_requires_confirmation_and_respects_answer(services, sandbox, fake_client):
    target = sandbox / "jarvis_test.txt"
    if target.exists():
        target.unlink()

    async def answer(approved: bool):
        for _ in range(100):
            if services.confirmations.pending:
                services.confirmations.respond(services.confirmations.pending[0].id, approved, fake_client.id)
                return
            await asyncio.sleep(0.01)

    # Abgelehnt -> Datei existiert nicht
    task = asyncio.create_task(services.tools.execute("create_file", {"path": str(target), "content": "hallo"}, client=fake_client))
    await answer(False)
    result = await task
    assert result["status"] == "cancelled"
    assert not target.exists()
    assert fake_client.of("confirm.request")

    # Bestätigt -> Datei wird angelegt
    task = asyncio.create_task(services.tools.execute("create_file", {"path": str(target), "content": "hallo"}, client=fake_client))
    await answer(True)
    result = await task
    assert result["status"] == "ok"
    assert target.read_text() == "hallo"

    # Überschreiben ist nicht möglich
    task = asyncio.create_task(services.tools.execute("create_file", {"path": str(target), "content": "neu"}, client=fake_client))
    await answer(True)
    assert (await task)["status"] == "error"
    assert target.read_text() == "hallo"


async def test_confirmation_from_other_client_rejected(services, sandbox, fake_client):
    task = asyncio.create_task(services.tools.execute("delete_file", {"path": str(sandbox / "x.txt")}, client=fake_client))
    for _ in range(100):
        if services.confirmations.pending:
            break
        await asyncio.sleep(0.01)
    pending = services.confirmations.pending[0]
    assert services.confirmations.respond(pending.id, True, "someone-else") is False
    services.confirmations.respond(pending.id, False, fake_client.id)
    assert (await task)["status"] == "cancelled"


async def test_memory_roundtrip(services):
    result = await services.tools.execute("remember", {"content": "Mein Lieblingsgetränk ist Espresso", "category": "preference"})
    assert result["status"] == "ok"
    recalled = await services.tools.execute("recall_memories", {"query": "Espresso"})
    assert any("Espresso" in m["content"] for m in recalled["result"]["memories"])
    block = await services.memory.context_block()
    assert "Espresso" in block


async def test_reminder_creation(services):
    result = await services.tools.execute("create_reminder", {"text": "Tee", "in_minutes": 5})
    assert result["status"] == "ok"
    bad = await services.tools.execute("create_reminder", {"text": "Tee"})
    assert bad["status"] == "error"


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Erste Meldung aus Wien</title><link>https://example.org/1</link><description>&lt;p&gt;Text&lt;/p&gt;</description><pubDate>Fri, 25 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>Zweite Meldung</title><link>https://example.org/2</link><pubDate>Fri, 25 Sep 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


class _Resp:
    status_code = 200
    content = RSS


async def test_news_feed_parsing(services, monkeypatch):
    async def fake_get(url, params=None):
        return _Resp()

    monkeypatch.setattr(services.web, "get", fake_get)
    items = await sources.fetch_feed(services.web, "Test", "https://example.org/rss")
    assert items[0]["title"] == "Erste Meldung aus Wien"
    assert items[0]["summary"] == "Text"
    assert items[0]["published"].startswith("2026-09-25")
    result = await services.tools.execute("get_news", {"topic": "Wien", "limit": 5})
    assert result["status"] == "ok"
    assert all("Wien" in i["title"] for i in result["result"]["items"])


async def test_xml_bomb_rejected(services, monkeypatch):
    class Bomb:
        status_code = 200
        content = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'

    async def fake_get(url, params=None):
        return Bomb()

    monkeypatch.setattr(services.web, "get", fake_get)
    import pytest

    from jarvis.tools.base import ToolError

    with pytest.raises(ToolError):
        await sources.fetch_feed(services.web, "Bomb", "https://example.org/rss")
