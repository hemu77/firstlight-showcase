FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pipelines pipelines
COPY services services
RUN useradd --uid 10001 --create-home firstlight && mkdir lake && chown firstlight:firstlight lake
USER firstlight
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "services.predictions:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
