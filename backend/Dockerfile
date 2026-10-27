FROM python:3.12-slim
WORKDIR /app/backend
COPY requirements.txt requirements-ml.txt ./
ARG WITH_ML=false
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$WITH_ML" = "true" ]; then pip install --no-cache-dir -r requirements-ml.txt --extra-index-url https://download.pytorch.org/whl/cpu; fi
COPY app ./app
COPY scripts ./scripts
ENV DATA_DIR=/data
# run unprivileged: a parser exploit then lands as an ordinary user with write access to /data only
RUN useradd --system --no-create-home --uid 10001 docudiff && mkdir -p /data && chown docudiff /data
USER docudiff
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
