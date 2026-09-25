FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 EIS223_DATA_DIR=/data SCAN_INTERVAL_SECONDS=300 GOSPLAN_DETAILS_PER_CYCLE=6 MAX_DOCS_PER_DETAIL=200 MAX_ATTACHMENT_DOWNLOADS=200 MAX_FILE_MB=50
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus antiword libarchive-tools && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN mkdir -p /data/files /data/reports
EXPOSE 8080
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
