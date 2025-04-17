from docker import DockerClient
import os
import sys
import threading
import signal
from pyroute2 import IPRoute
from pyroute2.netlink.exceptions import NetlinkError
from NetworkManager import *
from sdbus_block.networkmanager.enums import DeviceType


ipr = IPRoute()
default_route = ''
docker = DockerClient(base_url='unix://var/run/docker.sock')

def signal_handler(sig, frame):
    if sig in [signal.SIGKILL, signal.SIGINT]:
        shutdown()

def init():
    global default_route
    containers = set(container.name for container in docker.containers.list(filters={'label':'dhcp=true'})).intersection(container.name for network in docker.networks.list(filters={'driver':'bridge'}, greedy=True) for container in network.containers)
    routes = ipr.get_default_routes()
    if len(routes) > 0:
        # ASSUME that get_default_routes actually returns routes in metric order -- I'm not sure that's guaranteed!
        link = ipr.get_links(routes[0].get('OIF'))
        default_route = link[0].get('ifname')
        [publish_IP(default_route, container) for container in containers]

def delete_interfaces(containers):
    print ('deleting interfaces')
    connections = ' '.join(containers)
    os.system(f'nmcli connection delete {connections}')

def shutdown():
    containers = set(container.name for container in docker.containers.list(filters={'label':'traefik.enable=true'})).intersection(container.name for network in docker.networks.list(filters={'driver':'bridge'}, greedy=True) for container in network.containers)
    delete_interfaces(containers)
    ipr.release()
    print ('shutting down')
    sys.exit(0)
    
def network_changed(env, msg):
    index = msg['index']
    action = msg['event']
    interface = msg.get('ifname')
    if action == 'RTM_DELLINK' or action == 'RTM_DELROUTE':
        print(interface)
    if action == 'RTM_DELLINK' and interface == default_route:
        init()

def publish_IP(default_route, container):
    cmd = f'nmcli connection add ifname {container} con-name {container} save no type macvlan dev {default_route} mode vepa -- +ipv4.dhcp-hostname {container}'
    print (cmd)
    ret_code = os.system(cmd)

def unpublish_IP(container):
    os.system(f'nmcli connection delete {container}')

def docker_event_thread(name):
    for event in docker.events(filters={'type':'network'}, decode=True):
        attributes = event['Actor']['Attributes']
        if attributes['type'] == 'bridge':
            container = docker.containers.get(attributes['container'])
            if event['Action'] == 'connect':
                publish_IP(default_route, container.name)
            else:
                unpublish_IP(container.name)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    init()
    
    ipr.register_callback(network_changed)

    thread = threading.Thread(target=docker_event_thread, args=(1,))
    thread.start()
    thread.join()
