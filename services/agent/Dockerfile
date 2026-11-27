FROM python:3.12-slim
WORKDIR /code
COPY services/agent/requirements.txt .
RUN pip install --no-cache-dir --timeout 60 -r requirements.txt
COPY services/agent/ .
COPY src/ourney_travelsync/replacement_intent.py .
COPY src/ourney_travelsync/planning_settings_intent.py .
EXPOSE 8005
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
