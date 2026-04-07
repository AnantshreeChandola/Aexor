FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg and cryptography
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Download ONNX model for VectorIndex embeddings (all-MiniLM-L6-v2, 384-dim)
RUN mkdir -p /app/models && \
    curl -fSL -o /app/models/model.onnx \
      "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx"

ENV ONNX_MODEL_PATH=/app/models/model.onnx

# Install production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install dev/test dependencies so tests can run in-container
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov

COPY . .

EXPOSE 8000

CMD ["uvicorn", "shared.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
