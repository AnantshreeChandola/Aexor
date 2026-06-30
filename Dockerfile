FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg, cryptography, and Node.js (for Claude Code CLI)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Download ONNX models for VectorIndex embeddings and Tool Discovery reranking
RUN mkdir -p /app/models && \
    curl -fSL -o /app/models/model.onnx \
      "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx" && \
    curl -fSL -o /app/models/cross_encoder.onnx \
      "https://huggingface.co/cross-encoder/ms-marco-MiniLM-L-6-v2/resolve/main/onnx/model.onnx"

ENV ONNX_MODEL_PATH=/app/models/model.onnx
ENV CROSS_ENCODER_MODEL_PATH=/app/models/cross_encoder.onnx

# Install production dependencies from pyproject.toml
COPY pyproject.toml README.md ./
COPY components/ components/
COPY shared/ shared/
RUN pip install --no-cache-dir .

# Install dev/test dependencies so tests can run in-container
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov

COPY . .

EXPOSE 8000

CMD ["uvicorn", "shared.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
