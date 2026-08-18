FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY bot/pyproject.toml /app/
RUN pip install --no-cache-dir -e .
COPY bot/ /app/

CMD ["python", "-m", "botapp.main"]
