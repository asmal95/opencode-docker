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
            project_response = await client.post(
                f"{settings.OPENCODE_API_URL}/project/init",
                json={}
            )
            project_response.raise_for_status()
            project_id = project_response.json().get("id")

            if not project_id:
                await message.answer("Failed to create OpenCode project")
                return

            session_response = await client.post(
                f"{settings.OPENCODE_API_URL}/project/{project_id}/session",
                json={"directory": "/workspace"}
            )
            session_response.raise_for_status()
            session_data = session_response.json()
            session_id = session_data.get("id")

            if not session_id:
                await message.answer("Failed to create OpenCode session")
                return

            response = await client.post(
                f"{settings.OPENCODE_API_URL}/project/{project_id}/session/{session_id}/message",
                json={
                    "parts": [{"type": "text", "text": message.text}]
                }
            )
            response.raise_for_status()

            response_data = response.json()

            parts = response_data.get("parts", [])
            response_text = "\n".join(
                item.get("text", "") for item in parts if item.get("type") == "text"
            )

            if response_text:
                for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
                    await message.answer(chunk)
            else:
                await message.answer("No response from OpenCode")

            await client.delete(f"{settings.OPENCODE_API_URL}/project/{project_id}/session/{session_id}")

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await message.answer(f"Error: {str(e)}")
