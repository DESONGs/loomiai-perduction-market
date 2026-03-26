FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir kagglehub

COPY download_data.py prepare.py run_data_pipeline.py /app/

CMD ["python", "run_data_pipeline.py"]
