# How Telegram Bot Interacts with OpenCode

## Architecture Overview

```
User Message (Telegram)
        │
        ▼
Telegram Bot (aiogram)
        │
        │ 1. POST /sessions (create session)
        ▼
OpenCode API (port 4096)
        │
        │ 2. POST /sessions/{id}/messages
        ▼
AI Provider (any OpenAI-compatible API)
        │
        │ 3. AI Response
        ▼
OpenCode API (port 4096)
        │
        │ 4. Response data
        ▼
Telegram Bot (aiogram)
        │
        │ 5. POST /sendMessage (split into 4096-char chunks)
        ▼
Telegram API
        │
        ▼
User Reply
```

## Detailed Flow

### 1. User Sends Message to Telegram Bot
- User types a message in Telegram chat
- Message is received by Telegram servers
- Telegram sends webhook/polling event to bot

### 2. Telegram Bot Receives Message
- **File**: `sidecars/telegram-bot/handlers/message_handler.py`
- **Function**: `handle_message(message)`
- **Library**: aiogram 3.4.1 (Telegram bot framework)

```python
@router.message()
async def handle_message(message: types.Message):
    user_message = message.text
```

### 3. Bot Creates OpenCode Session
- **Endpoint**: `POST {OPENCODE_API_URL}/sessions`
- **Library**: httpx 0.27.0 (Async HTTP client)
- **Timeout**: 300 seconds

```python
session_response = await client.post(
    f"{settings.OPENCODE_API_URL}/sessions",
    json={}  # Config comes from mounted opencode.jsonc
)
```

**What happens:**
- OpenCode creates a new conversation session
- Applies configuration from `configs/bot/opencode.jsonc`
- Returns session ID for subsequent requests

### 4. Bot Sends User Message to OpenCode
- **Endpoint**: `POST {OPENCODE_API_URL}/sessions/{session_id}/messages`
- **Method**: HTTP POST with JSON payload

```python
response = await client.post(
    f"{settings.OPENCODE_API_URL}/sessions/{session_id}/messages",
    json={
        "role": "user",
        "content": [{"type": "text", "text": message.text}]
    }
)
```

**What happens:**
- OpenCode processes the message using the configured OpenAI-compatible provider
- Applies permissions (write/edit/bash: ask)
- Uses configured tools
- Returns AI response in structured format

### 5. OpenCode Processes Request
- **Configuration**: Read from `/opt/opencode-config/opencode.jsonc`
- **Provider**: Uses configured `openai-compatible` provider
- **Permissions**: Applies security policies
- **Tools**: Uses allowed tools (read, grep, glob, bash, etc.)

### 6. AI Provider Generates Response
- Processes with the configured model
- Returns text/structured data

### 7. OpenCode Returns Response
- **Format**: JSON with message content
- **Structure**:
```json
{
  "message": {
    "content": [
      {
        "type": "text",
        "text": "AI response here..."
      }
    ]
  }
}
```

### 8. Bot Parses Response
- **File**: `sidecars/telegram-bot/handlers/message_handler.py`
- **Logic**: Extracts text content from structured response

```python
assistant_message = response_data.get("message", {}).get("content", [])
response_text = "\n".join(
    item.get("text", "") for item in assistant_message if item.get("type") == "text"
)
```

### 9. Bot Sends Response to User
- **Library**: aiogram message handling
- **Limit**: Telegram message limit (4096 characters)
- **Splitting**: Long messages are split into chunks

```python
for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
    await message.answer(chunk)
```

### 10. Session Cleanup
- **Endpoint**: `DELETE {OPENCODE_API_URL}/sessions/{session_id}`
- **Purpose**: Cleanup session resources

```python
await client.delete(f"{settings.OPENCODE_API_URL}/sessions/{session_id}")
```

## Network Architecture

### Docker Network Setup

```yaml
services:
  opencode:
    ports:
      - "4096:4096"
    networks:
      - opencode-net

  telegram-bot:
    environment:
      OPENCODE_API_URL: http://opencode:4096
    networks:
      - opencode-net
```

### Communication Flow
1. **Internal DNS**: Docker resolves `opencode` to container IP
2. **Port 4096**: OpenCode serves API on this port
3. **HTTP Protocol**: REST API over HTTP
4. **JSON Format**: Request/response data in JSON

## Provider Configuration

### OpenAI-Compatible Provider

```jsonc
{
  "providers": {
    "openai-compatible": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "{env:OPENAI_COMPATIBLE_BASE_URL}",
        "apiKey": "{env:OPENAI_COMPATIBLE_API_KEY}"
      }
    }
  }
}
```

Works with any OpenAI-compatible API:
- **OpenRouter**: `https://api.openrouter.ai/v1`
- **Ollama**: `http://host.docker.internal:11434/v1`
- **llama.cpp**: `http://YOUR_IP:8080/v1`
- **LM Studio**: `http://localhost:1234/v1`

## Permissions Configuration

```jsonc
{
  "permissions": {
    "write": "allow",
    "edit": "allow",
    "bash": "ask"
  }
}
```

## Tools Configuration

```jsonc
{
  "tools": {
    "allowed": ["*"]
  }
}
```

## Error Handling

### Bot Error Handling

```python
try:
    # API calls
except Exception as e:
    logger.error(f"Error processing message: {e}")
    await message.answer(f"Error: {str(e)}")
```

### Common Error Scenarios
1. **Session Creation Failure**: Invalid config, provider unreachable
2. **Message Processing Failure**: API timeout, provider error
3. **Response Parsing Failure**: Invalid JSON, missing fields
4. **Network Issues**: Container connectivity, DNS resolution

## Performance Considerations

### Timeout Settings

```python
async with httpx.AsyncClient(timeout=300.0) as client:
    # 5-minute timeout for AI responses
```

### Message Size Limits
- **Telegram**: 4096 characters per message
- **Splitting**: Long responses are chunked
- **AI Models**: Can generate longer responses

## Security Architecture

### Container Isolation

```yaml
read_only: true
tmpfs:
  - /tmp
security_opt:
  - no-new-privileges:true
cap_drop:
  - ALL
cap_add:
  - CHOWN
  - SETUID
  - SETGID
  - NET_BIND_SERVICE
```

### Network Security

```yaml
extra_hosts:
  - "app.opencode.ai:0.0.0.0"
  - "api.opencode.ai:0.0.0.0"
  - "opncd.ai:0.0.0.0"
  - "models.dev:0.0.0.0"
```

### API Key Management

```bash
# Environment variables only
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=sk-or-xxx
```

## Monitoring and Debugging

### View Bot Logs

```bash
docker compose logs -f telegram-bot
```

### View OpenCode Logs

```bash
docker compose logs -f opencode
```

### Test API Directly

```bash
# Create session
curl -X POST http://localhost:4096/sessions \
  -H "Content-Type: application/json" \
  -d '{}'

# Send message
curl -X POST http://localhost:4096/sessions/{id}/messages \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":[{"type":"text","text":"Hello"}]}'
```

## Troubleshooting

### Common Issues

**Bot not responding:**
```bash
docker compose logs telegram-bot
# Check: Telegram token, OpenCode connectivity
```

**Session creation fails:**
```bash
docker compose logs opencode
# Check: Provider config, API keys
```

**Long response times:**
```bash
# Check: AI provider performance, network latency
```

### Provider Connectivity

```bash
# From host
curl http://YOUR_BASE_URL/v1/models

# From container
docker exec opencode curl http://YOUR_BASE_URL/v1/models
```

## Summary

The Telegram bot acts as a **bridge** between:
- **User** (Telegram chat interface)
- **OpenCode** (AI processing engine)
- **AI Providers** (any OpenAI-compatible API)

It handles:
- ✅ Message reception from Telegram
- ✅ Session management with OpenCode
- ✅ API communication (HTTP/JSON)
- ✅ Response parsing and formatting
- ✅ Message size limitation handling
- ✅ Error handling and logging
- ✅ Session cleanup

This architecture provides a **clean separation of concerns**:
- Telegram bot focuses on chat interface
- OpenCode handles AI processing
- Providers execute model inference
- Any OpenAI-compatible API works out of the box
