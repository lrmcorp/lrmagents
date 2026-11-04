# LRMagent voice: speech-to-text (faster-whisper) and text-to-speech (Kokoro) for the
# personal assistant and WhatsApp voice replies. CPU only; models load on first use
# into the voice_models volume (checksummed where the publisher gives fixed files).
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
RUN useradd -u 998 -m lrmvoice && mkdir -p /models && chown lrmvoice:lrmvoice /models
USER lrmvoice
ENV MODELS=/models PORT=8095 HF_HUB_DISABLE_TELEMETRY=1 OMP_NUM_THREADS=2
EXPOSE 8095
CMD ["python", "server.py"]
