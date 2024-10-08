# container-dns-helper

A simple NetworkManager helper to insert all of your Docker container names into your LAN's DNS

## Rationale

I want to be able to find any container on my host machine by name just as I would find the host itself. 

There are a number of methods available to create mDNS names for your docker containers. These would be aliases for the host machine. `hardillb/traefik-avahi-helper` was the best I found, 
but in my experience mDNS was less than robust. 
Sometimes the names wouldn't be published. At others, I could see the names from my local server but not from any other machine on the LAN. It's probably my misconfiguration, but I found it 
frustrating, and figured there had to be an easy way to get my local router to add an 'alias'. But, it's **not** simple. If you tell DHCP to assign an IP for a new host name, it'll match on 
the interface's MAC address, and just rename your machine, rather than creating a new one.

This approach uses NetworkManager's `nmcli` to create a new `macvlan` interface as a child of your primary Internet-facing ethernet NIC. A `macvlan` interface has a new MAC address, 
different from anything existing on your host. Therefore, when NetworkManager brings it up, it will get assigned a new IP—even though traffic will actually flow through the parent interface.

So, if my host machine is named **server.home** and has an IP address of 192.168.1.10, a container named **mycontainer** might be given the name **mycontainer.home** and assigned 192.168.1.15.
The Avahi/mDNS method would have made them **server.local** and **mycontainer.local** and both names would translate to 192.168.1.10, but the important detail is that in either case, everything is 
sent to 192.168.1.10 and standard reverse proxies can route to the correct container.

## Getting Started

```shell
git clone  https://github.com/auspex/container-dns-helper.git
cd container-dns-helper
```

### Prerequisities

In order to run this container you'll need docker installed.

* [Windows](https://docs.docker.com/windows/started)
* [OS X](https://docs.docker.com/mac/started/)
* [Linux](https://docs.docker.com/linux/started/)

To be honest, I see no likelihood of this ever working on Windows, and not much chance of OS X :-)

You also need to be running a version of Linux that uses NetworkManager.

### Usage

From the directory of your git clone:

```shell
docker compose up -d --build
```

#### Container Parameters

The compose file runs with `security_opt` `apparmor:unconfined` to permit communication with DBUS.

#### Environment Variables
None yet

#### Volumes

* `/var/run/docker.sock` - Needed for access to the Docker container and network information
* `/var/run/dbus` - so that `nmcli`  can communicate with the host's NetworkManager

## ToDo

I've not managed to get the script to recognize when the parent device is disconnected and a new (or the same) interface connects. As long as your connections are stable, this should
work on any router with a properly compliant DHCP server.

## Built With

* docker.io/library/python:3.13.0 

## Find Us

* [GitHub](https://github.com/auspex/container-dns-helper)

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct, and the process for submitting pull requests to us.

## Authors

* **Derek Broughton** - *Initial work* - [PointerStop](https://github.com/auspex)

## License

This project is licensed under the GNU GPL v3 License - see the [LICENSE](LICENSE) file for details.
