import docker
import os
import sys
import threading
import signal
from pyroute2 import IPDB
ipdb = IPDB()
default_route = ipdb.interfaces[ipdb.routes['default']['oif']]['ifname']
client = docker.DockerClient(base_url='unix://var/run/docker.sock')

def signal_handler(sig, frame):
    if sig in [signal.SIGKILL, signal.SIGINT]:
        shutdown()

def init():
    delete_interfaces()
    default_route = ipdb.interfaces[ipdb.routes['default']['oif']]['ifname']
    [publish_IP(default_route, container.name) for network in client.networks.list(filters={'driver':'bridge'}, greedy=True) for container in network.containers]

def delete_interfaces():
    print ('deleting interfaces')
    # os.system(f'ip link delete group containers')
    connections = ' '.join(['container-'+container.name for network in client.networks.list(filters={'driver':'bridge'}, greedy=True) for container in network.containers])
    os.system(f'nmcli connection delete {connections}')

def shutdown():
    delete_interfaces()
    ipdb.release()
    print ('shutting down')
    sys.exit(0)
    
def network_changed(ip, msg, action):
    index = msg['index']
    interface = ip.interfaces[index].ifname
    if action == 'RTM_DELLINK' or action == 'RTM_DELROUTE':
        print(interface)
    if action == 'RTM_DELLINK' and interface == default_route:
        init()

def publish_IP(default_route, container):
    cmd = f'nmcli connection add type macvlan ifname {container} con-name container-{container} autoconnect yes save no dev {default_route} mode vepa ipv4.dhcp-hostname {container}'
    print (cmd)
    ret_code = os.system(cmd)

def unpublish_IP(container):
    os.system(f'nmcli connection delete container-{container}')

def docker_event_thread(name):
    for event in client.events(filters={'type':'network'}, decode=True):
        attributes = event['Actor']['Attributes']
        if attributes['type'] == 'bridge':
            container = client.containers.get(attributes['container'])
            if event['Action'] == 'connect':
                publish_IP(default_route, container.name)
            else:
                unpublish_IP(container.name)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    init()
    
    ipdb.register_callback(network_changed)
    thread = threading.Thread(target=docker_event_thread, args=(1,))
    thread.start()
    thread.join()
