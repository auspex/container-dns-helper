FROM python

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# You need network-manager to get nmcli, but you DON'T WANT NetworkManager running in your container
# from https://docs.balena.io/reference/OS/network/#changing-the-network-at-runtime
RUN apt update && apt install -y network-manager iproute2 && systemctl mask NetworkManager.service.

COPY . .

CMD  [ "python3", "./AddToDns.py" ]