# The real recommender (team package: scoring, AWM, GFAR) behind the HTTP contract. Port 8002.
# Build context = repository root (it needs the package in src/):
#   docker build -f services/recommender/Dockerfile .
FROM python:3.13-slim
WORKDIR /app

# Install dependencies before copying source, so code/data edits reuse this layer.
COPY pyproject.toml README.md ./
COPY services/recommender/requirements.txt ./requirements.txt
RUN python -c "import tomllib; from pathlib import Path; p=tomllib.loads(Path('pyproject.toml').read_text())['project']; semantic=p['optional-dependencies']['semantic']; Path('/tmp/torch-cpu.txt').write_text(next(d for d in semantic if d.startswith('torch==')) + '+cpu\n'); Path('/tmp/package-requirements.txt').write_text('\n'.join(p['dependencies'] + semantic))"
# E5 runs on CPU. Select the CPU wheel explicitly and keep it constrained below.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu -r /tmp/torch-cpu.txt
RUN pip install --no-cache-dir -c /tmp/torch-cpu.txt -r /tmp/package-requirements.txt -r requirements.txt

COPY src ./src
RUN pip install --no-cache-dir --no-deps . && pip check
RUN python -c "import torch; assert torch.version.cuda is None, 'Expected CPU-only PyTorch'"
COPY services/recommender/main.py ./main.py

EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
