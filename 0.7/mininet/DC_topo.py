# -*- coding: utf-8 -*-
"""Datacenter topology.
   Consists of two core switches, one switch of access layer and leaf one switch per segment

   _________terminate_switch_____________________
        |                       |
      core_sw----------------core_sw
         |                      |
    ------------------------------------------------------
        |        |                  |           |
      leaf_sw1  leaf_sw2   ....   leaf_sw_n   leaf_sw_n+1
        |        |                    |           |
      servers   servers             servers      servers

"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.node import RemoteController, OVSSwitch
from functools import partial

class MyTopo( Topo ):
    "Simple topology example."

    def __init__( self, leaf_sw_am, serv_per_sw, ips):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        ts1 = self.addSwitch( 's1', dpid='%x' % 11)
        cs1 = self.addSwitch( 'cs1', dpid='%x' % 12)
        cs2 = self.addSwitch( 'cs2', dpid='%x' % 13)

        self.addLink( ts1, cs1, 2, 1 )
        self.addLink( ts1, cs1, 3, 1 )

        cs_pnum = 2
        ip_num = 0
        max_ip_num = len(ips)
        ip_addr = []
        for i in range (max_ip_num):
            ip_addr.append(10)

        for i in range(1, leaf_sw_am+1):
            # create leaf sw and add hosts from one segment to it
            s = self.addSwitch( 'ls%s'%i, dpid='%x' % (13+i))
            serv_pnum = 3
            self.addLink( s, cs1, 1, cs_pnum )
            self.addLink( s, cs2, 2, cs_pnum )
            cs_pnum += 1
            # add servers
            for j in range(1, serv_per_sw+1):
                ip_addr[ip_num] += 1
                serv = self.addHost( 'serv%s-%s'%(i,j), ip=str( ips[ip_num]+'%s/24'%ip_addr[ip_num] ) )
                self.addLink( serv, s, 0, serv_pnum )
                serv_pnum += 1
            ip_num += 1
            if ip_num >= max_ip_num:
                ip_num = 0
            

def runMinimalTopo():
    CONTROLLER_IP = '192.168.2.4'
    leaf_sw_am = 6
    serv_am = 5
    ips = ['172.16.24.', '172.16.0.', '172.16.16.', '172.16.28.', '172.16.40.', '172.16.32.']

    topo = MyTopo(leaf_sw_am, serv_am, ips)

    net = Mininet(topo = topo,
        controller=lambda name: RemoteController( name, ip=CONTROLLER_IP),
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        autoSetMacs=True )
    net.start()
    
    ip_num = 0
    max_ip_num = len(ips)

    for i in range(1, leaf_sw_am+1):
        for j in range(1, serv_am+1):
            net.get('serv%s-%s'%(i,j) ).cmd('ip route add default via '+ ips[ip_num]+'1')
        ip_num += 1
        if ip_num >= max_ip_num:
            ip_num = 0

    net.get('s1').cmd('ovs-vsctl add-port s1 eth1')

    cli = CLI(net)
    # After the user exits the CLI, shutdown the network.
    net.stop()


if __name__ == '__main__':
    # This runs if this file is executed directly
    setLogLevel( 'info' )
    runMinimalTopo()

topos = { 'mytopo': MyTopo }