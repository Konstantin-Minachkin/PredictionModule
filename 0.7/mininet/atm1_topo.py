# -*- coding: utf-8 -*-
"""Cash register topology

   terminate switch
      |       |
      hosts  switch
              |
              hosts

    Hosts consist of ATMs and Security devices (camers, etc)

"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.node import RemoteController, OVSSwitch
from functools import partial

class MyTopo( Topo ):
    "Simple topology example."

    def __init__( self, leaf_sw_am, serv_per_sw, camers_per_sw):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        ts1 = self.addSwitch( 's1', dpid='%x' % 81)
        
        atm_addr = 131
        cam_addr = 4

        for i in range(2, leaf_sw_am+2):
            # create leaf sw and add hosts from one segment to it
            s = self.addSwitch( 's%s'%i, dpid='%x' % (80+i))
            pnum = 2
            self.addLink( s, ts1, 1, i )
            # add hosts
            for j in range(1, camers_per_sw):
                serv = self.addHost( 'cam%s-%s'%(i,j), ip='172.16.29.%s/27'%cam_addr )
                cam_addr += 1
                self.addLink( serv, s, 0, pnum )
                pnum += 1
            for j in range(1, serv_per_sw):
                serv = self.addHost( 'atm%s-%s'%(i,j), ip='172.16.26.%s/26'%atm_addr )
                atm_addr += 1
                self.addLink( serv, s, 0, pnum )
                pnum += 1


def runMinimalTopo():
    CONTROLLER_IP = '192.168.2.4'
    leaf_sw_am = 2
    serv_am = 5
    cameras_am = 1
    topo = MyTopo(leaf_sw_am, serv_am, cameras_am)

    net = Mininet(topo = topo,
        controller=lambda name: RemoteController( name, ip=CONTROLLER_IP),
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        autoSetMacs=True )
    net.start()


    for i in range(2, leaf_sw_am+2):
        for j in range(1, cameras_am):
            net.get('cam%s-%s'%(i,j) ).cmd('ip route add default via 172.16.29.1')
        for j in range(1, serv_am):
            net.get('atm%s-%s'%(i,j) ).cmd('ip route add default via 172.16.26.129')

    net.get('s1').cmd('ovs-vsctl add-port s1 eth1')

    cli = CLI(net)
    # After the user exits the CLI, shutdown the network.
    net.stop()


if __name__ == '__main__':
    # This runs if this file is executed directly
    setLogLevel( 'info' )
    runMinimalTopo()

topos = { 'mytopo': MyTopo }