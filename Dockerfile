FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/recipes.db

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
