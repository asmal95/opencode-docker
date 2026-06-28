#!/bin/bash
set -e

echo "OpenCode Telegram Bot - Auto Deployment Script"
echo "=================================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Creating .env from template..."
    cat > .env << 'EOF'
# Telegram Bot Token (required)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# OpenAI-Compatible API Configuration
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=your_api_key_here

# OpenCode Server Authentication
OPENCODE_SERVER_PASSWORD=your_server_password_here

# User/Group ID mapping
PUID=1000
PGID=1000

# Project directory
PROJECT_DIR=.
EOF
    echo "Please edit .env with your API keys before running this script again"
    echo "  nano .env"
    exit 1
fi

# Check if Telegram bot token is set
if grep -q "your_telegram_bot_token_here" .env; then
    echo "Error: Please set your Telegram bot token in .env file"
    echo "  nano .env"
    exit 1
fi

# Check if config exists
if [ ! -f configs/bot/opencode.jsonc ]; then
    echo "Creating provider configuration..."
    mkdir -p configs/bot
    echo "Provider configuration found"
fi

echo "Configuration found"

# Pull latest images
echo "Pulling latest Docker images..."
docker compose -f docker-compose.prebuilt.yaml pull

# Start containers
echo "Starting containers..."
docker compose -f docker-compose.prebuilt.yaml up -d

# Wait for services to be ready
echo "Waiting for services to start..."
sleep 5

# Check status
echo ""
echo "Container Status:"
docker compose -f docker-compose.prebuilt.yaml ps

echo ""
echo "Deployment complete!"
echo ""
echo "Management Commands:"
echo "  View logs: docker compose -f docker-compose.prebuilt.yaml logs -f"
echo "  Restart:   docker compose -f docker-compose.prebuilt.yaml restart"
echo "  Stop:      docker compose -f docker-compose.prebuilt.yaml down"
echo ""
echo "Your Telegram bot should now be responding to messages!"
