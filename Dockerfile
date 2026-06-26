# Base stage - minimal headless environment
FROM debian:bookworm-slim AS opencode-base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    bash \
    ca-certificates \
    ripgrep \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js LTS (Node.js 22.x)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Python 3 and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install opencode-ai with cache busting support
ARG OPENCODE_BUILD_TIME=0
RUN npm install -g opencode-ai@latest

# Install gosu for privilege dropping
RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create user directory structure
RUN mkdir -p /home/coder/.local/share/opencode \
    && mkdir -p /home/coder/.cache/opencode \
    && mkdir -p /home/coder/.config/opencode \
    && useradd -m -s /bin/bash coder

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set environment variables to disable unwanted features
ENV OPENCODE_DISABLE_AUTOUPDATE=true
ENV OPENCODE_DISABLE_MODELS_FETCH=true
ENV OPENCODE_DISABLE_SHARE=true

# Create MCP servers directory
RUN mkdir -p /opt/mcp-servers

# Switch to coder user
USER coder
WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["opencode"]