#! /usr/bin/python3
 
import asyncio

from aiodocker.docker import Docker

import sdbus
from sdbus_async.networkmanager import (
    NetworkConnectionSettings,
    NetworkManager,
    NetworkManagerSettings,
    )

import signal
from time import sleep

sdbus.set_default_bus(sdbus.sd_bus_open_system())
nm = NetworkManager()

async def signal_handler(sig, tasks):
    """
    Exit cleanly on SIGTERM ("docker stop"), SIGINT (^C when interactive)
    (see https://stackoverflow.com/a/79612074/334719)
    """
    print(f"\n> Caught signal: {sig.name}")
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    print("> Shutdown complete.")

async def container_names(docker: Docker) -> list:
    """
    Get names of all Docker containers having the label `dhcp=true` and using a `bridge` network interface
    """
    network_names = [network['Name'] for network in (await docker.networks.list(filters={'driver':['bridge']}))]
    containers = await docker.containers.list(filters={
        'label': ['dhcp=true'],
        'status': ['running'],
        'network': network_names,
        })
    names = [(await container.show())["Name"].replace('/','') for container in containers]
    return names

def get_default_route() -> str:
    from pyroute2 import IPRoute
    ipr = IPRoute()

    # ASSUME that get_default_routes actually returns routes in metric order
    # -- I'm not sure that's guaranteed!
    routes = ipr.get_default_routes()
    ifname = None
    if len(routes) > 0:
        link = ipr.get_links(routes[0].get('OIF'))
        ifname = link[0].get('ifname')

        print(f'Default route: {ifname}')

        from os import environ
        import re

        # get ALLOWED_DEVICES, replacing '*' with '.*' and comma with '|' for regex
        allowed_devices = '^'+environ.get('ALLOWED_DEVICES','*').replace(',','|^').replace('*','.*')
        print(f'Allowed: {allowed_devices}')
        # if the interface doesn't match the allowed devices, return None
        if re.match(allowed_devices, ifname):
            # if it's allowed, check that it isn't DISallowed
            disallowed_devices = environ.get('DISALLOWED_DEVICES')
            print(f'Disallowed: {disallowed_devices}')
            # if there are DISALLOWED_DEVICES, and the interface matches any of them, return None
            if disallowed_devices and re.match('^'+disallowed_devices.replace(',','|^').replace('*','.*'), ifname):
                ifname = None
        else:
            ifname = None

    ipr.release()

    return ifname

async def watch_for_disconnect(parent: str) -> None:
    """
    Watch for disconnection on the default route. If it disconnects, exit and let Docker restart the container
    q.v. https://github.com/aio-libs/aiodocker/blob/main/examples/events.py
    """
    from sdbus_async.networkmanager.enums import DeviceState
    from sdbus_async.networkmanager import NetworkDeviceGeneric

    try:
        device_path = await nm.get_device_by_ip_iface(parent)
        generic_device = NetworkDeviceGeneric(device_path)
        async for (
            new_state,
            old_state,
            reason,
        ) in generic_device.state_changed.catch():
            if DeviceState(new_state) == DeviceState.DISCONNECTED:
                print(f"Now {DeviceState(new_state).name}, was {DeviceState(old_state).name}")
                # use the signal handler to terminate the other thread (it's fine if it kills this one too!)
                signal.raise_signal(signal.SIGTERM)
    except asyncio.CancelledError:
        pass

async def publish_all(parent):
    """
    - create a NetworkManager connection for each required container
    - watch the docker socket for network connect/disconnect events
    - before the container is stopped, remove the NM connections & exit
    """
    try:
        docker = Docker()
        [await publish_IP(parent, container) for container in (await container_names(docker))]
        await docker_event_loop(docker, parent)
    except asyncio.exceptions.CancelledError:
        pass
    print ('> shutting down')
    [await unpublish_IP(container) for container in (await container_names(docker))]
    await docker.close()

async def docker_event_loop(docker: Docker, parent: str) -> None:
    """
    Watch for docker network events and add or remove containers to/from the host DNS as required.
    """
    subscriber = docker.events.subscribe(filters={'event':['connect','disconnect']})
    while True:
        event = await subscriber.get()
        attributes = event['Actor']['Attributes']
        if attributes['type'] == 'bridge':
            container = await docker.containers.get(attributes['container'])
            data = await container.show()
            container_name = data['Name'].replace('/', '')
            labels = data['Config']['Labels']
            if labels.get('dhcp') == 'true':
                if event['Action'] == 'connect':
                    if len(container_name) > 10:
                        print (f"Can't publish DNS for container names longer than 10 characters: {container_name}")
                    else:
                        await publish_IP(parent, container_name)
                else:
                    await unpublish_IP(container_name)

async def publish_IP(parent, container):
    """
    Create a Macvlan connection named `container`, with `parent` as the parent, 
    and get an IPv4 address via DHCP

    Functionally equivalent to:
        nmcli connection add ifname {container} con-name {container} save no type macvlan dev {parent} mode vepa -- +ipv4.dhcp-hostname {container}
    """
    from sdbus_async.networkmanager.settings import (
        ConnectionProfile,
        ConnectionSettings,
        Ipv4Settings, 
        MacvlanSettings,
        )
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
        
    # Now, NetworkManager should have a profile, but you still have to activate it
    new_path = await nm.activate_connection(path)
    print(f'add ifname {container} dev {parent}: {new_path}')
    return new_path

async def unpublish_IP(container):
    connection_paths = await NetworkManagerSettings().get_connections_by_id(container)
    if len(connection_paths) > 0:
        path = connection_paths[0]
        connection = NetworkConnectionSettings(path)
        await connection.delete()
        print(f'delete {container}: {path}')

async def main(parent):
    tasks = [
        asyncio.create_task(watch_for_disconnect(parent)),
        asyncio.create_task(publish_all(parent)),
        ]
    loop = asyncio.get_running_loop()
    for s in [signal.SIGTERM, signal.SIGINT]:
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(signal_handler(s, tasks)))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    parent = get_default_route()

    if parent is None:
        # If we have no network, wait 60s before exiting. Docker will restart after that
        print('Internet not found')
        sleep(60)
    else:
        asyncio.run(main(parent))
