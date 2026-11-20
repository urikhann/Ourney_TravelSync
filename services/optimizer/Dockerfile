# Build from repository root: docker build -f services/optimizer/Dockerfile .
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY services/optimizer/requirements.txt ./requirements.txt
RUN python -c "import tomllib; from pathlib import Path; p=tomllib.loads(Path('pyproject.toml').read_text())['project']; Path('/tmp/package-requirements.txt').write_text('\n'.join(p['dependencies'] + p['optional-dependencies']['optimizer']))"
RUN pip install --no-cache-dir -r /tmp/package-requirements.txt -r requirements.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && pip check
COPY services/optimizer/main.py ./main.py
EXPOSE 8003
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
