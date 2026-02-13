FROM python:3.12-slim
ENV PYTHONIOENCODING utf-8

COPY /src /code/src/
COPY /tests /code/tests/
COPY /scripts /code/scripts/
COPY requirements.txt /code/requirements.txt
COPY flake8.cfg /code/flake8.cfg
COPY deploy.sh /code/deploy.sh

# install gcc to be able to build packages - e.g. required by regex, dateparser, also required for pandas
RUN apt-get update && apt-get install -y build-essential \
    xvfb \
    xauth

RUN pip install flake8

RUN pip install -r /code/requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-unifont fonts-liberation fonts-noto-color-emoji \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2t64 libatspi2.0-0t64 libwayland-client0 libxfixes3 \
    && rm -rf /var/lib/apt/lists/*
RUN playwright install chromium

# workaround from https://github.com/stitionai/devika/issues/297
RUN useradd -m -s /bin/bash myuser
USER myuser
RUN playwright install chromium

WORKDIR /code/

CMD ["sh", "-c", "Xvfb :99 -nolisten tcp -nolisten unix -screen 0 1024x768x24 & export DISPLAY=:99 && python -u /code/src/component.py"]
