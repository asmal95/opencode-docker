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
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js LTS (Node.js 22.x)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install opencode-ai with cache busting support
ARG OPENCODE_BUILD_TIME=0
RUN npm install -g opencode-ai@latest

# Create user directory structure with proper ownership
RUN mkdir -p /home/coder/.local/share/opencode \
    && mkdir -p /home/coder/.local/state \
    && mkdir -p /home/coder/.cache/opencode \
    && mkdir -p /home/coder/.config/opencode \
    && mkdir -p /workspace \
    && chmod 1777 /workspace \
    && useradd -m -s /bin/bash coder \
    && chown -R coder:coder /home/coder \
    && chown coder:coder /workspace

# Set environment variables to disable unwanted features
ENV OPENCODE_DISABLE_AUTOUPDATE=true
ENV OPENCODE_DISABLE_MODELS_FETCH=true
ENV OPENCODE_DISABLE_SHARE=true

# Create MCP servers directory
RUN mkdir -p /opt/mcp-servers

# Switch to non-root user
USER coder
WORKDIR /workspace

CMD ["opencode"]