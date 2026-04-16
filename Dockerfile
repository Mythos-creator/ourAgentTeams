FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required by presidio / spacy / chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better layer caching
COPY requirements.txt pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Download spacy language model for presidio-analyzer
RUN python -m spacy download en_core_web_lg

# Copy project source
COPY src/ ./src/
COPY config/ ./config/

# Create required data directories
RUN mkdir -p data/tasks data/sessions data/memory data/vectorstore

# Default entrypoint — allows running any CLI command
ENTRYPOINT ["python", "-m", "src.cli.main"]
CMD ["--help"]
