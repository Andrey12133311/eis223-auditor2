FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 EIS223_DATA_DIR=/data SCAN_INTERVAL_SECONDS=300 GOSPLAN_DETAILS_PER_CYCLE=12 MAX_DOCS_PER_DETAIL=200 MAX_ATTACHMENT_DOWNLOADS=200 MAX_FILE_MB=50
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus antiword libarchive-tools libreoffice-writer && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY scripts/ scripts/
RUN python -m py_compile scripts/v310_archive_scan.py scripts/v310_archive_selftest.py scripts/v298_eis_only.py scripts/v295_eis_originals.py scripts/v299_browser_intake.py scripts/v314_eis_metadata.py scripts/v319_manual_inputs.py scripts/v301_search_jobs.py scripts/v257_features.py scripts/v304_official_memo_odt.py scripts/v321_memo_layout.py scripts/v257_features.py scripts/v302_eis_modern_ui.py scripts/v311_verified_customer.py scripts/v323_memo_manual_selftest.py && PYTHONPATH=/app/scripts python scripts/v310_archive_selftest.py && PYTHONPATH=/app/scripts python scripts/v312_manifest_selftest.py && PYTHONPATH=/app/scripts python scripts/v323_memo_manual_selftest.py
RUN mkdir -p /data/files /data/reports
EXPOSE 8080
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
