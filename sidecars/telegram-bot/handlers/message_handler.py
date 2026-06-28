#!/usr/bin/env python3
import base64
import logging
import httpx
from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from config import settings

logger = logging.getLogger(__name__)
router = Router()

# Per-chat session tracking
_session_map: dict[int, str] = {}


def _auth_header() -> dict[str, str]:
    credentials = f"opencode:{settings.OPENCODE_SERVER_PASSWORD}"
    return {"Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"}


def _get_client() -> httpx.AsyncClient:
    headers = _auth_header()
    return httpx.AsyncClient(
        base_url=settings.OPENCODE_API_URL,
        timeout=httpx.Timeout(300.0, connect=10.0),
        headers=headers,
    )


async def get_or_create_session(chat_id: int) -> str | None:
    if chat_id in _session_map:
        return _session_map[chat_id]
    # Try to find an existing session for this chat (by title)
    client = _get_client()
    try:
        resp = await client.get("/session")
        resp.raise_for_status()
        sessions = resp.json()
        title = f"chat:{chat_id}"
        for s in sessions:
            if s.get("title", "").startswith(title):
                _session_map[chat_id] = s["id"]
                return s["id"]
    except Exception as e:
        logger.error(f"Error finding session for chat {chat_id}: {e}")
    finally:
        await client.aclose()
    return None


async def create_session(chat_id: int) -> str | None:
    if chat_id in _session_map:
        del _session_map[chat_id]
    title = f"chat:{chat_id}"
    client = _get_client()
    try:
        body = {"title": title}
        params = {}
        if settings.PROJECT_DIR:
            params["directory"] = settings.PROJECT_DIR
        resp = await client.post("/session", json=body, params=params)
        resp.raise_for_status()
        session = resp.json()
        sid = session.get("id")
        if sid:
            _session_map[chat_id] = sid
        return sid
    except Exception as e:
        logger.error(f"Error creating session for chat {chat_id}: {e}")
        return None
    finally:
        await client.aclose()


def _format_session_list(sessions: list) -> str:
    lines = ["Доступные сессии:"]
    for i, s in enumerate(sessions, 1):
        sid = s.get("id", "?")
        stitle = s.get("title", "Без названия") or "Без названия"
        stime = s.get("time", {}).get("updated", 0)
        lines.append(f"{i}. {stitle} ({sid[:20]}...)")
    lines.append(f"\nВсего: {len(sessions)}")
    return "\n".join(lines)


@router.message(CommandStart())
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


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_start(message)


@router.message(Command("new"))
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


@router.message(Command("abort"))
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
    client = _get_client()
    try:
        resp = await client.post(f"/session/{sid}/abort")
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
    finally:
        await client.aclose()


@router.message(Command("sessions"))
async def cmd_sessions(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        await message.answer("Доступ запрещён")
        return
    client = _get_client()
    try:
        resp = await client.get("/session")
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
    finally:
        await client.aclose()


@router.message(Command("clean"))
async def cmd_clean(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        await message.answer("Доступ запрещён")
        return
    client = _get_client()
    deleted_count = 0
    try:
        resp = await client.get("/session")
        resp.raise_for_status()
        sessions = resp.json()
        title_prefix = f"chat:{chat_id}"
        to_delete = [s for s in sessions if s.get("title", "").startswith(title_prefix)]
        if not to_delete:
            await message.answer("Нет сессий для удаления")
            return
        for s in to_delete:
            try:
                dresp = await client.delete(f"/session/{s['id']}")
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
    finally:
        await client.aclose()


@router.message()
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    allowed = settings.allowed_chat_ids_set
    if allowed and chat_id not in allowed:
        return
    sid = await get_or_create_session(chat_id)
    if not sid:
        await message.answer("Нет сессий. Создайте /new")
        return
    # Check if session is busy
    client = _get_client()
    try:
        resp = await client.get("/session/status")
        resp.raise_for_status()
        status_map = resp.json()
        if status_map.get(sid, {}).get("type") == "busy":
            await message.answer("Сессия сейчас занята. Подождите или используйте /abort")
            return
    except Exception as e:
        logger.error(f"Error checking session status: {e}")
    finally:
        await client.aclose()
    # Send message
    client = _get_client()
    try:
        resp = await client.post(
            f"/session/{sid}/message",
            json={"parts": [{"type": "text", "text": message.text}]},
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
    finally:
        await client.aclose()
    # Parse response
    parts = response_data.get("parts", [])
    text_parts = [p for p in parts if p.get("type") == "text"]
    if text_parts:
        response_text = text_parts[0].get("text", "")
        if response_text:
            for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
                await message.answer(chunk)
            return
    await message.answer("Нет текстового ответа от OpenCode")
