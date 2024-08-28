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

RUN playwright install --with-deps chromium

WORKDIR /code/

CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 & export DISPLAY=:99 && python -u /code/src/component.py"]