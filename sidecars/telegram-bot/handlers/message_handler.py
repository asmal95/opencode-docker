#!/usr/bin/env python3
import logging
import httpx
from aiogram import Router, types
from config import settings

logger = logging.getLogger(__name__)
router = Router()

@router.message()
async def handle_message(message: types.Message):
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            session_response = await client.post(
                f"{settings.OPENCODE_API_URL}/sessions",
                json={}
            )
            session_data = session_response.json()
            session_id = session_data.get("id")
            
            if not session_id:
                await message.answer("Failed to create OpenCode session")
                return
            
            response = await client.post(
                f"{settings.OPENCODE_API_URL}/sessions/{session_id}/messages",
                json={
                    "role": "user",
                    "content": [{"type": "text", "text": message.text}]
                }
            )
            
            response_data = response.json()
            
            assistant_message = response_data.get("message", {}).get("content", [])
            response_text = "\n".join(
                item.get("text", "") for item in assistant_message if item.get("type") == "text"
            )
            
            if response_text:
                for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
                    await message.answer(chunk)
            else:
                await message.answer("No response from OpenCode")
            
            await client.delete(f"{settings.OPENCODE_API_URL}/sessions/{session_id}")
            
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await message.answer(f"Error: {str(e)}")
