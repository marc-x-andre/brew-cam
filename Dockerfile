FROM python:3.15-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Pipfile Pipfile.lock ./
RUN pip install --no-cache-dir pipenv \
    && pipenv install --deploy --ignore-pipfile --system

COPY . .

CMD python -m brewcam --config "${BREWCAM_CONFIG:-config/session.example.yaml}"
