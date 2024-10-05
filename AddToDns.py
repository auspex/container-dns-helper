import docker
import os
import sys
import threading
import subprocess
import signal

def signal_handler(sig, frame):
    if sig in [signal.SIGKILL, signal.SIGINT]:
        print ('deleting interfaces')
        os.system(f'ip link delete group containers')
        print ('shutting down')
        sys.exit(0)


def publish_IP(iface, container):
    ip_link = f'ip link add link {iface} {container.name} type macvlan'
    print (ip_link)
    if os.system(ip_link) == 0:
        os.system(f'ip link set {container.name} up group containers')
        dhcp = f'udhcpc -i {container.name} -q -x hostname:{container.name}'
        print(dhcp)
        os.system(dhcp)

def unpublish_IP(container):
    os.system(f'ip link delete name {container.name}')

def event_thread(name):
    for event in client.events(filters={'type':'network'}, decode=True):
        attributes = event['Actor']['Attributes']
        if attributes['type'] == 'bridge':
            container = client.containers.get(attributes['container'])
            if event['Action'] == 'connect':
                publish_IP(iface, container)
            else:
                unpublish_IP(container)

if __name__ == "__main__":
    iface = subprocess.run("ip route | egrep '^default' | grep metric | cut -f5 -d' '", 
                           shell=True,
                           text=True, 
                           capture_output=True, 
                           check=True).stdout.splitlines()[0]
    
    client = docker.DockerClient(base_url='unix://var/run/docker.sock')

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for network in client.networks.list(filters={'driver':'bridge'}, greedy=True):
        [publish_IP(iface, container) for container in network.containers]

    thread = threading.Thread(target=event_thread, args=(1,))
    thread.start()
