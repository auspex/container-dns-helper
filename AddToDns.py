import os
import signal
import sys
import threading
import asyncio

import sdbus
from docker import DockerClient
from pyroute2 import IPRoute
from sdbus_async.networkmanager import (
    NetworkConnectionSettings,
    NetworkManager,
    NetworkManagerSettings,
    )
from sdbus_async.networkmanager.settings import (
    ConnectionProfile,
    ConnectionSettings,
    Ipv4Settings, 
    MacvlanSettings,
    )

sdbus.set_default_bus(sdbus.sd_bus_open_system())

ipr = IPRoute()
docker = DockerClient(base_url='unix://var/run/docker.sock')
NM = NetworkManager()
class ShutdownRequested(BaseException): pass

def signal_handler(sig, frame):
    if sig in [signal.SIGKILL, signal.SIGINT]:
        raise ShutdownRequested

def container_names():
    ### get names of all Docker containers having the label `dhcp=true` and using a `bridge`
    return set(
        container.name for container in docker.containers.list(filters={'label':'dhcp=true'})
    ).intersection(
        container.name for network in docker.networks.list(filters={'driver':'bridge'}, greedy=True) for container in network.containers
    )

def default_route():
    # ASSUME that get_default_routes actually returns routes in metric order
    # -- I'm not sure that's guaranteed!
    routes = ipr.get_default_routes()
    ifname = None
    if len(routes) > 0:
        link = ipr.get_links(routes[0].get('OIF'))
        ifname = link[0].get('ifname')
    return ifname

async def init():
    containers = container_names()
    parent = default_route()
    if parent is not None:
        [await publish_IP(parent, container) for container in containers]

    try:
        for event in docker.events(filters={'type':'network'}, decode=True):
            if ShutdownRequested: 
                break
            attributes = event['Actor']['Attributes']
            if attributes['type'] == 'bridge':
                container = docker.containers.get(attributes['container'])
                if container.labels['dhcp'] == 'true':
                    if event['Action'] == 'connect':
                        await publish_IP(default_route(), container.name)
                    else:
                        await unpublish_IP(container.name)
    except ShutdownRequested:
        print ('shutting down')

    [await unpublish_IP(container) for container in container_names()]
    ipr.release()
    
async def publish_IP(default_route, container):
    """
    Create a Macvlan connection named `container`, with `default_route` as the parent, 
    and get an IPv4 address via DHCP

    Functionally equivalent to:
        nmcli connection add ifname {container} con-name {container} save no type macvlan dev {default_route} mode vepa -- +ipv4.dhcp-hostname {container}
    """
    print(f'nmcli connection add ifname {container} con-name {container} save no type macvlan dev {default_route} mode vepa -- +ipv4.dhcp-hostname {container}')
    connection_paths = await NetworkManagerSettings().get_connections_by_id(container)
    if len(connection_paths) == 0:
        # create a new Macvlan device
        # # make the new connection Macvlan, with autoconnect
        profile = ConnectionProfile(
            connection=ConnectionSettings(
                autoconnect=True,
                connection_id=container,
                connection_type='macvlan',
                interface_name=container
            ),
            macvlan=MacvlanSettings(
                parent=default_route,
                mode=1 # vepa
            ),
            # # don't forget to tell NetworkManager to request an IP for this hostname
            ipv4=Ipv4Settings(
                dhcp_hostname=container,
                method='auto',
                )
        )
        path = await NetworkManagerSettings().add_connection_unsaved(profile.to_dbus())
        # Now, NetworkManager should have a profile, but you still have to activate it
        await NM.activate_connection(path)
    else:
        # modify existing device
        # (need to find the .../Settings object from the .../ActiveConnection object)
        connection = NetworkConnectionSettings(connection_paths[0])

        settings = await connection.get_settings()
        settings['connection']['id'] = container
        settings['connection']['interface-name'] = container
        settings['connection']['autoconnect'] = True
        settings['macvlan']['parent'] = default_route
        settings['ipv4']['dhcp-hostname'] = container
        settings.update()
        # might need to deactivate before re-activating
        await NM.activate_connection(connection_paths[0])

async def unpublish_IP(container):
    print(f'nmcli connection delete {container}')
    connection_paths = await NetworkManagerSettings().get_connections_by_id(container)
    if len(connection_paths) > 0:
        connection = NetworkConnectionSettings(connection_paths[0])
        await connection.delete()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    asyncio.run(init())
    # init()
    
    # ipr.register_callback(network_changed)
