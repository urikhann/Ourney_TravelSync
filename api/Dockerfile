FROM python:3.13-slim
WORKDIR /code
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
# API imports typed contracts and independent E3; it does not need OR-Tools.
RUN pip install --no-cache-dir .
COPY api/app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
