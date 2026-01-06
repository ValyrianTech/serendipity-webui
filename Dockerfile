# Stage 1: Build stage - install dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

# Install dependencies into a virtual environment for easy copying
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Stage 2: Runtime stage - minimal image
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY app/ ./app/
COPY static/ ./static/
COPY entrypoint.sh .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose the application port
EXPOSE 8080

# Run as non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

ENTRYPOINT ["./entrypoint.sh"]
