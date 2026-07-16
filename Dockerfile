FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if required (psycopg binary is pre-compiled, so we only need basic tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend source folders
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create a folder for SQLite persistent storage
RUN mkdir -p /app/data

# Configure environment variables
ENV DATABASE_PATH=/app/data/postgresql_mon.db
ENV PYTHONUNBUFFERED=1

# Expose server port
EXPOSE 8000

# Run the app
CMD ["python", "-m", "backend.main"]
