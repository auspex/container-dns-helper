#! /usr/bin/python3
 
import signal
import asyncio

import sdbus
from docker import DockerClient
from pyroute2 import IPRoute, NDB
from sdbus_async.networkmanager import (
    NetworkConnectionSettings,
    NetworkManager,
    NetworkDeviceGeneric,
    NetworkManagerSettings,
    )
from sdbus_async.networkmanager.settings import (
    ConnectionProfile,
    ConnectionSettings,
    Ipv4Settings, 
    MacvlanSettings,
    )
from sdbus.utils import parse

from pyroute2.netlink.rtnl.ifinfmsg import ifinfmsg

sdbus.set_default_bus(sdbus.sd_bus_open_system())

ipr = IPRoute()
docker = DockerClient(base_url='unix://var/run/docker.sock')
NM = NetworkManager()

class ShutdownRequested(BaseException): pass

def signal_handler(sig, frame):
    """
    Exit cleanly on SIGTERM ("docker stop"), SIGINT (^C when interactive)
    """
    if sig in [signal.SIGINT, signal.SIGTERM]:
        raise ShutdownRequested

def container_names():
    """
    Get names of all Docker containers having the label `dhcp=true` and using a `bridge` network interface
    """
    return set(
        container.name for container in docker.containers.list(filters={'label':'dhcp=true'})
    ).intersection(
        container.name for network in docker.networks.list(filters={'driver':'bridge'}, greedy=True) for container in network.containers
    )

def get_default_route():
    # ASSUME that get_default_routes actually returns routes in metric order
    # -- I'm not sure that's guaranteed!
    routes = ipr.get_default_routes()
    ifname = None
    if len(routes) > 0:
        link = ipr.get_links(routes[0].get('OIF'))
        ifname = link[0].get('ifname')
    return ifname

async def watch_for_disconnect(iface):
    """
    Watch for disconnection on the default route. If it disconnects, signal RouteChanged to rebuild the macvlans

    NB. This isn't working!
    """
    devices = [NetworkDeviceGeneric(x) for x in (await NM.get_devices())]
    paths = [await x.active_connection for x in devices if await x.interface==iface]

    # settings = NetworkManagerSettings()
    # async for x in settings.connection_removed:
    #     print(x)

    connection = NetworkConnectionSettings(paths[0])
    try:
        async for x in connection.removed:
            print (x)
            # If the default route changes, just shut down the container, and let Docker restart it
            raise ShutdownRequested
    except asyncio.exceptions.CancelledError:
        pass

async def init():
    """
    - start a second task just to wait for a disconnect on the parent interface,
    - create a NetworkManager connection for each required container
    - watch the docker socket for network connect/disconnect events
    - if the parent interface goes away, or the container is stopped, remove the NM connections & exit
    """
    parent = get_default_route()

    if parent is not None:
        task = asyncio.create_task(watch_for_disconnect(parent))
        # try:
        # except asyncio.CancelledError:
        try:
            [await publish_IP(parent, container) for container in container_names()]
            await docker_event_loop(parent)
        except ShutdownRequested:
            task.cancel()
            print ('shutting down')
            [await unpublish_IP(container) for container in container_names()]
            await task

async def docker_event_loop(parent):
    """
    Watch for docker network events and add or remove containers to/from the host DNS as required.
    """
    for event in docker.events(filters={'type':'network'}, decode=True):
        attributes = event['Actor']['Attributes']
        if attributes['type'] == 'bridge':
            container = docker.containers.get(attributes['container'])
            if container.labels['dhcp'] == 'true':
                if event['Action'] == 'connect':
                    await publish_IP(parent, container.name)
                else:
                    await unpublish_IP(container.name)

async def publish_IP(parent, container):
    """
    Create a Macvlan connection named `container`, with `parent` as the parent, 
    and get an IPv4 address via DHCP

    Functionally equivalent to:
        nmcli connection add ifname {container} con-name {container} save no type macvlan dev {parent} mode vepa -- +ipv4.dhcp-hostname {container}
    """
    connection_paths = await NetworkManagerSettings().get_connections_by_id(container)
    path = ''
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
                parent=parent,
                mode=1 # vepa
            ),
            # # don't forget to tell NetworkManager to request an IP for this hostname
            ipv4=Ipv4Settings(
                dhcp_hostname=container,
                method='auto',
                )
        )
        path = await NetworkManagerSettings().add_connection_unsaved(profile.to_dbus())
    else:
        # modify existing device
        # (need to find the .../Settings object from the .../ActiveConnection object)
        path = connection_paths[0]
        connection = NetworkConnectionSettings(path)

        settings = await connection.get_settings()
        settings['connection']['id'] = container
        settings['connection']['interface-name'] = container
        settings['connection']['autoconnect'] = True
        settings['macvlan']['parent'] = parent
        settings['ipv4']['dhcp-hostname'] = container
        settings.update()
        # might need to deactivate before re-activating
        
    # Now, NetworkManager should have a profile, but you still have to activate it
    new_path = await NM.activate_connection(path)
    print(f'add ifname {container} dev {parent}: {new_path}')
    return new_path

async def unpublish_IP(container):
    connection_paths = await NetworkManagerSettings().get_connections_by_id(container)
    if len(connection_paths) > 0:
        path = connection_paths[0]
        connection = NetworkConnectionSettings(path)
        await connection.delete()
        print(f'delete {container}: {path}')

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    asyncio.run(init())

    ipr.release()
