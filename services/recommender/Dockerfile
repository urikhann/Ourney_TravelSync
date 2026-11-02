# The real recommender (team package: scoring, AWM, GFAR) behind the HTTP contract. Port 8002.
# Build context = repository root (it needs the package in src/):
#   docker build -f services/recommender/Dockerfile .
FROM python:3.13-slim
WORKDIR /app

# 1. The team package (numpy + the place CSVs that ship inside it)
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# 2. The service itself
COPY services/recommender/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/recommender/main.py ./main.py

EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
