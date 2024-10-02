FROM mcr.microsoft.com/playwright/python:v1.47.0-noble
ENV PYTHONIOENCODING=utf-8

COPY /src /code/src/
COPY /tests /code/tests/
COPY /scripts /code/scripts/
COPY requirements.txt /code/requirements.txt
COPY flake8.cfg /code/flake8.cfg
COPY deploy.sh /code/deploy.sh

# install gcc to be able to build packages - e.g. required by regex, dateparser, also required for pandas
RUN apt-get update && apt-get install -y build-essential \
    xvfb \
    xauth \
    xkb-data \
    x11-xkb-utils

RUN pip install flake8

RUN pip install -r /code/requirements.txt

WORKDIR /code/

CMD ["bash", "-c", "Xvfb :99 -nolisten tcp -nolisten unix -screen 0 1024x768x24 -quiet 2>/dev/null & export DISPLAY=:99 && python -u /code/src/component.py"]