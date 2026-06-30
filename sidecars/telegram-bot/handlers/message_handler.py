#!/usr/bin/env python3
import asyncio
import base64
import logging
import re
import httpx
from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandStart
from config import settings

logger = logging.getLogger(__name__)


def router(bot: Bot) -> Router:
    r = Router()
    r.message.register(cmd_start, CommandStart())
    r.message.register(cmd_help, Command("help"))
    r.message.register(cmd_new, Command("new"))
    r.message.register(cmd_abort, Command("abort"))
    r.message.register(cmd_sessions, Command("sessions"))
    r.message.register(cmd_clean, Command("clean"))
    r.message.register(handle_message)
    return r

# Telegram HTML parse mode only supports these tags. Any other tag causes
# "Bad Request: can't parse entities" errors when the AI returns raw HTML.
_SUPPORTED_TAGS = frozenset([
    "a", "emoji", "code", "pre", "b", "strong",
    "i", "em", "u", "ins", "s", "strike", "del",
    "blockquote",
])


def _sanitize_html(html: str) -> str:
    """Remove HTML start/end tags that Telegram's parse_mode does not support."""
    def _replace(m: re.Match) -> str:
        tag = m.group(1).lower()
        if tag in _SUPPORTED_TAGS:
            return m.group(0)
        return ""
    return re.sub(r"</?([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?/?>", _replace, html)

# Per-chat session tracking
_session_map: dict[int, str] = {}

_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        credentials = f"opencode:{settings.OPENCODE_SERVER_PASSWORD}"
        headers = {"Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"}
        _http_client = httpx.AsyncClient(
            base_url=settings.OPENCODE_API_URL,
            timeout=httpx.Timeout(300.0, connect=10.0),
            headers=headers,
        )
    return _http_client


async def close_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def get_or_create_session(chat_id: int) -> str | None:
    if chat_id in _session_map:
        return _session_map[chat_id]
    # Try to find an existing session for this chat (by title)
    try:
        resp = await _get_client().get("/session")
        resp.raise_for_status()
        sessions = resp.json()
        title = f"chat:{chat_id}"
        for s in sessions:
            if s.get("title", "").startswith(title):
                _session_map[chat_id] = s["id"]
                return s["id"]
    except Exception as e:
        logger.error(f"Error finding session for chat {chat_id}: {e}")
    return None


async def create_session(chat_id: int) -> str | None:
    if chat_id in _session_map:
        del _session_map[chat_id]
    title = f"chat:{chat_id}"
    body = {"title": title}
    params = {}
    if settings.PROJECT_DIR:
        params["directory"] = settings.PROJECT_DIR
    try:
        resp = await _get_client().post("/session", json=body, params=params)
        resp.raise_for_status()
        session = resp.json()
        sid = session.get("id")
        if sid:
            _session_map[chat_id] = sid
        return sid
    except httpx.HTTPStatusError as e:
        try:
            error_body = await e.response.aread()
            logger.error(f"POST /session error for chat {chat_id}: HTTP {e.response.status_code} — body='{error_body.decode()[:1000]}' — req_body={body} — req_url={e.request.url}")
        except Exception:
            logger.error(f"POST /session error for chat {chat_id}: HTTP {e.response.status_code} — body=<unreadable> — req_body={body}")
        return None
    except Exception as e:
        logger.error(f"Error creating session for chat {chat_id}: {e}")
        return None


def _format_session_list(sessions: list) -> str:
    lines = ["Доступные сессии:"]
    for i, s in enumerate(sessions, 1):
        sid = s.get("id", "?")
        stitle = s.get("title", "Без названия") or "Без названия"
        stime = s.get("time", {}).get("updated", 0)
        lines.append(f"{i}. {stitle} ({sid[:20]}...)")
    lines.append(f"\nВсего: {len(sessions)}")
    return "\n".join(lines)


async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я OpenCode AI-ассистент.\n\n"
        "Команды:\n"
        "/new — новая сессия\n"
        "/sessions — список сессий\n"
        "/abort — прервать текущую сессию\n"
        "/clean — удалить все сессии этого чата\n"
        "/help — справка"
    )


async def cmd_help(message: types.Message):
    await cmd_start(message)


async def cmd_new(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        await message.answer("Доступ запрещён")
        return
    sid = await create_session(chat_id)
    if sid:
        await message.answer(f"Новая сессия создана.\nID: {sid[:25]}...\nПиши!")
    else:
        await message.answer("Не удалось создать сессию")


async def cmd_abort(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        await message.answer("Доступ запрещён")
        return
    sid = _session_map.get(chat_id)
    if not sid:
        await message.answer("Нет активной сессии. Создайте /new")
        return
    try:
        resp = await _get_client().post(f"/session/{sid}/abort")
        resp.raise_for_status()
        await message.answer("Сессия прервана")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            del _session_map[chat_id]
            await message.answer("Сессия не найдена. Создайте /new")
        else:
            await message.answer(f"Ошибка: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Error aborting session {sid}: {e}")
        await message.answer(f"Ошибка прерывания: {str(e)}")


async def cmd_sessions(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        await message.answer("Доступ запрещён")
        return
    try:
        resp = await _get_client().get("/session")
        resp.raise_for_status()
        sessions = resp.json()
        title_prefix = f"chat:{chat_id}"
        my_sessions = [s for s in sessions if s.get("title", "").startswith(title_prefix)]
        if not my_sessions:
            await message.answer(f"У вас нет сессий. Создайте /new")
            return
        await message.answer(_format_session_list(my_sessions))
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        await message.answer(f"Ошибка: {str(e)}")


async def cmd_clean(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        await message.answer("Доступ запрещён")
        return
    deleted_count = 0
    try:
        resp = await _get_client().get("/session")
        resp.raise_for_status()
        sessions = resp.json()
        title_prefix = f"chat:{chat_id}"
        to_delete = [s for s in sessions if s.get("title", "").startswith(title_prefix)]
        if not to_delete:
            await message.answer("Нет сессий для удаления")
            return
        for s in to_delete:
            try:
                dresp = await _get_client().delete(f"/session/{s['id']}")
                if dresp.status_code == 200:
                    deleted_count += 1
                    if _session_map.get(chat_id) == s["id"]:
                        del _session_map[chat_id]
            except Exception as e:
                logger.error(f"Error deleting session {s['id']}: {e}")
        await message.answer(f"Удалено сессий: {deleted_count}")
    except Exception as e:
        logger.error(f"Error cleaning sessions: {e}")
        await message.answer(f"Ошибка: {str(e)}")


async def _typing_loop(bot: Bot, chat_id: int, stop: asyncio.Event) -> None:
    try:
        await bot.send_chat_action(chat_id, "typing")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass
            if not stop.is_set():
                await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass


async def handle_message(message: types.Message, bot: Bot):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        return
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_loop(bot, chat_id, stop_typing))
    try:
        sid = await get_or_create_session(chat_id)
        if not sid:
            await message.answer("Нет сессий. Создайте /new")
            return
        # Check if session is busy
        try:
            resp = await _get_client().get("/session/status")
            resp.raise_for_status()
            status_map = resp.json()
            if status_map.get(sid, {}).get("type") == "busy":
                await message.answer("Сессия сейчас занята. Подождите или используйте /abort")
                return
        except Exception as e:
            logger.error(f"Error checking session status: {e}")
        # Add chat_id to context via system hint
        chat_hint = f"\n\n[Chat ID: {chat_id} - use this in cron delivery chat_id]"
        full_text = message.text + chat_hint

        # Send message
        try:
            resp = await _get_client().post(
                f"/session/{sid}/message",
                json={"parts": [{"type": "text", "text": full_text}]},
            )
            resp.raise_for_status()
            response_data = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                del _session_map[chat_id]
                await message.answer("Сессия не найдена. Создайте /new")
            else:
                await message.answer(f"Ошибка: {e.response.status_code} — {str(e)}")
            return
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await message.answer(f"Ошибка отправки: {str(e)}")
            return
        # Parse response
        parts = response_data.get("parts", [])
        text_parts = [p for p in parts if p.get("type") == "text"]
        if text_parts:
            response_text = text_parts[0].get("text", "")
            if response_text:
                for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
                    await message.answer(_sanitize_html(chunk))
                    await bot.send_chat_action(chat_id, "typing")
                    await asyncio.sleep(0.5)
                return
        await message.answer("Нет текстового ответа от OpenCode")
    finally:
        stop_typing.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
