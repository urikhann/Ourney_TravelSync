# Build from repository root: docker build -f services/optimizer/Dockerfile .
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[optimizer]'
COPY services/optimizer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/optimizer/main.py ./main.py
EXPOSE 8003
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
