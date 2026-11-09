FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
ENV DB_PATH=/data/boxes.db CATALOG_PATH=/data/catalog.csv
EXPOSE 8080
# One process on purpose: boxes then pick one at a time and never waste a model call on clashing picks.
# (Duplicates are impossible either way; the database enforces that.)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
