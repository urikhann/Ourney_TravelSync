FROM python:3.13-slim
WORKDIR /code
COPY services/planning_data/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/planning_data /code
COPY src/ourney_travelsync/operating_schedules.py /code/operating_schedules.py
COPY src/ourney_travelsync/provisional_schedules.py /code/provisional_schedules.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
