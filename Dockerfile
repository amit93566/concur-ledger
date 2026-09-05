FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schema.sql .
COPY app ./app

# Single worker on purpose: the service is async, one event loop handles the
# concurrency, and multiple worker processes would add a second source of
# interleaving that has nothing to do with the database behaviour under test.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
