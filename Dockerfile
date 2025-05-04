FROM python:3.13.3-slim

WORKDIR /app

COPY requirements.txt ./

# suppress the warning about pip running as root
RUN pip install --root-user-action=ignore --no-cache-dir -r requirements.txt

COPY . .

CMD  [ "python3", "./AddToDns.py" ]