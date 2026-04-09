FROM python:3.11-slim

# Set timezone to Eastern — critical for market-hours logic in main.py
ENV TZ=America/New_York
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Persistent journal lives here — Railway volume mounts to this path
RUN mkdir -p /app/journal_data

CMD ["python", "crypto_main.py"]
