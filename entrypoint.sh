#!/usr/bin/env bash
set -e

# Get the UID/GID from environment variables or use defaults
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Check if we need to modify the coder user
if [ "$(id -u coder)" != "$PUID" ]; then
    echo "Setting coder user UID to $PUID"
    usermod -u "$PUID" coder
fi

if [ "$(id -g coder)" != "$PGID" ]; then
    echo "Setting coder user GID to $PGID"
    groupmod -g "$PGID" coder
fi

# Ensure home directory ownership
chown -R coder:coder /home/coder

# Ensure workspace directory exists and has correct permissions
if [ ! -d "/workspace" ]; then
    mkdir -p /workspace
fi
chown -R coder:coder /workspace

# Drop privileges and execute the command
exec gosu coder "$@"