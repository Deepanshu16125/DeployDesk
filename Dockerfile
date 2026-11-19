FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 HF_HOME=/app/.cache/hf DEPLOYDESK_CONFIG=/app/config/customer.yaml

# cpu-only torch, otherwise pip pulls ~2GB of CUDA wheels nobody here can use
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# bake both small models in so the container starts without internet at the customer site
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

COPY deploydesk ./deploydesk
COPY config ./config
COPY eval ./eval
COPY data/raw ./data/raw
COPY docker/entrypoint.sh /entrypoint.sh

# index the demo docs at build time so first start doesn't spend 15s embedding
RUN python -m deploydesk.cli ingest

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "deploydesk.api:app", "--host", "0.0.0.0", "--port", "8000"]
