FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schema.sql .
COPY app ./app

# Single worker by default, on purpose: the service is async, one event loop
# handles the concurrency, and multiple worker processes would add a second
# source of interleaving that has nothing to do with the database behaviour
# under test. Experiments 1, 2 and 4 always run with 1 (the crash test relies on
# the container, not a uvicorn master, restarting the process).
#
# UVICORN_WORKERS > 1 exists for Experiment 3 only, where one Python process is
# the throughput ceiling at short operations; each worker opens its own pool of
# POOL_MAX_SIZE connections. Reported by /config and recorded in every row.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}"]
