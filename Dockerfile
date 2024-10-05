FROM python:alpine

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN apk add --no-cache iproute2 busybox

CMD  [ "python", "./AddToDns.py" ]