#!/usr/bin/env python3
import base64
import logging
import httpx
from aiogram import Router, types
from config import settings

logger = logging.getLogger(__name__)
router = Router()

def _auth_header():
    credentials = f"opencode:{settings.OPENCODE_SERVER_PASSWORD}"
    return f"Basic {base64.b64encode(credentials.encode()).decode()}"

@router.message()
async def handle_message(message: types.Message):
    try:
        headers = {"Authorization": _auth_header()}
        async with httpx.AsyncClient(timeout=300.0, headers=headers) as client:
            sessions_response = await client.get(
                f"{settings.OPENCODE_API_URL}/session",
                headers=headers
            )
            sessions_response.raise_for_status()
            sessions = sessions_response.json()

            if sessions:
                session = sessions[0]
                session_id = session.get("id")
                session_directory = session.get("directory", "/workspace")
            else:
                import uuid
                session_id = f"ses_{uuid.uuid4().hex[:24]}"
                session_directory = "/workspace"

            response = await client.post(
                f"{settings.OPENCODE_API_URL}/session/{session_id}/message",
                json={
                    "directory": session_directory,
                    "parts": [{"type": "text", "text": message.text}]
                }
            )
            response.raise_for_status()

            response_data = response.json()
            parts = response_data.get("parts", [])
            text_parts = [p for p in parts if p.get("type") == "text"]

            response_text = ""
            if text_parts:
                response_text = text_parts[0].get("text", "")

            if response_text:
                for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
                    await message.answer(chunk)
            else:
                await message.answer("No response from OpenCode")

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await message.answer(f"Error: {str(e)}")
