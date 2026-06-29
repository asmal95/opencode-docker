#!/bin/bash
set -e

echo "OpenCode Telegram Bot - Auto Deployment Script"
echo "=================================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Creating .env from .env.example..."
    if [ -f .env.example ]; then
        cp .env.example .env
    else
        echo "No .env.example template found. Please create .env with your credentials."
        exit 1
    fi
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
    echo "Warning: configs/bot/opencode.jsonc not found. The bot may not work."
    echo "Please create the config file or copy from configs/base/opencode.jsonc"
else
    echo "Configuration found"
fi

# Pull latest images
echo "Pulling latest Docker images..."
docker compose -f docker-compose.deploy.yaml pull

# Start containers
echo "Starting containers..."
docker compose -f docker-compose.deploy.yaml up -d

# Wait for services to be ready
echo "Waiting for services to start..."
sleep 5

# Check status
echo ""
echo "Container Status:"
docker compose -f docker-compose.deploy.yaml ps

echo ""
echo "Deployment complete!"
echo ""
echo "Management Commands:"
echo "  View logs: docker compose -f docker-compose.deploy.yaml logs -f"
echo "  Restart:   docker compose -f docker-compose.deploy.yaml restart"
echo "  Stop:      docker compose -f docker-compose.deploy.yaml down"
echo ""
echo "Your Telegram bot should now be responding to messages!"
