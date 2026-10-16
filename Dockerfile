FROM python:3.13-slim

# Install system dependencies: FFmpeg and fonts required for subtitles / drawtext filters
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 10001 -s /bin/bash showrunner

WORKDIR /app

# Install Python production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and static assets
COPY app/ ./app/
COPY static/ ./static/
COPY LICENSE ./
COPY README.md ./

# Create default data storage directory and assign ownership
RUN mkdir -p /data && chown -R showrunner:showrunner /data /app

USER showrunner

# Default runtime configuration
ENV DATA_DIR=/data \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
