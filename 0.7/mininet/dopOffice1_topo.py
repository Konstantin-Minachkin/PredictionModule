# -*- coding: utf-8 -*-
"""Region office topology.
    Office has tro floors

   _________terminate_switch_____________________
      |       |                     |
             switch-1-floor  switch-2-floor
              |                |         |
             hosts          switchF2     hosts
                               |
                              hosts

    Hosts consist of PC+Phones, ATMs or Security devices (camers, etc)

"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.node import RemoteController, OVSSwitch
from functools import partial

class MyTopo( Topo ):
    "Simple topology example."

    def __init__( self, hosts_per_sw, hnum, tnum):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        ts1 = self.addSwitch( 's1', dpid='%x' % 41)
        s_fl1 = self.addSwitch( 's_fl1', dpid='%x' % 42)
        s_fl2 = self.addSwitch( 's_fl2', dpid='%x' % 43)
        s_f2_2 = self.addSwitch( 's_f2_2', dpid='%x' % 44)
        
        self.addLink( ts1, s_fl1, 2, 1 )
        self.addLink( ts1, s_fl2, 3, 1 )
        self.addLink( s_fl2, s_f2_2, 2, 1 )

        sec1 = self.addHost( 'sec1', ip='172.16.28.2/27')
        sec2 = self.addHost( 'sec2', ip='172.16.28.3/27') 	
        self.addLink( sec1, s_fl1, 0, 2 )
        self.addLink( sec2, s_fl2, 0, 3 )

        atm1 = self.addHost( 'atm1', ip='172.16.26.18/28')
        atm2 = self.addHost( 'atm2', ip='172.16.26.19/28')
        atm3 = self.addHost( 'atm3', ip='172.16.26.20/28')
        self.addLink( atm2, s_f2_2, 0, 2 )
        self.addLink( atm3, s_f2_2, 0, 3 )
        self.addLink( atm1, s_fl1, 0, 3 )

        pnum = 4
        # Add links halv of hosts to one switch - half to another
        for i in range(hosts_per_sw):
            h = self.addHost( 'h%s'%hnum, ip='172.16.128.%s/26'%hnum)
            t = self.addHost( 't%s'%tnum, ip='172.16.128.%s/26'%tnum)
            self.addLink( h, s_fl1, 0, pnum )
            self.addLink( t, s_fl1, 0, pnum+1 )
            hnum+=1
            tnum+=1
            h = self.addHost( 'h%s'%hnum, ip='172.16.128.%s/26'%hnum)
            t = self.addHost( 't%s'%tnum, ip='172.16.128.%s/26'%tnum)
            self.addLink( h, s_fl2, 0, pnum )
            self.addLink( t, s_fl2, 0, pnum+1 )
            hnum+=1
            tnum+=1
            h = self.addHost( 'h%s'%hnum, ip='172.16.128.%s/26'%hnum)
            t = self.addHost( 't%s'%tnum, ip='172.16.128.%s/26'%tnum)
            self.addLink( h, s_f2_2, 0, pnum )
            self.addLink( t, s_f2_2, 0, pnum+1 )
            hnum+=1
            tnum+=1
            pnum += 2
            

def runMinimalTopo():
    CONTROLLER_IP = '192.168.2.4'
    num = 4
    hnum = 130
    tnum = 194
    topo = MyTopo(num, hnum, tnum) # Create an instance of our topology

    net = Mininet(topo = topo,
        controller=lambda name: RemoteController( name, ip=CONTROLLER_IP),
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        autoSetMacs=True )
    net.start()
    
    for i in range(num):
        net.get('h%s'%hnum).cmd('ip route add default via 172.16.128.129')
        net.get('t%s'%tnum).cmd('ip route add default via 172.16.128.193')
        hnum+=1
        tnum+=1
        net.get('h%s'%hnum).cmd('ip route add default via 172.16.128.129')
        net.get('t%s'%tnum).cmd('ip route add default via 172.16.128.193')
        hnum+=1
        tnum+=1
        net.get('h%s'%hnum).cmd('ip route add default via 172.16.128.129')
        net.get('t%s'%tnum).cmd('ip route add default via 172.16.128.193')
        hnum+=1
        tnum+=1
        
    net.get('sec1').cmd('ip route add default via 172.16.28.33')
    net.get('sec2').cmd('ip route add default via 172.16.28.33')

    net.get('atm1').cmd('ip route add default via 172.16.26.17')
    net.get('atm2').cmd('ip route add default via 172.16.26.17')
    net.get('atm3').cmd('ip route add default via 172.16.26.17')

    net.get('s1').cmd('ovs-vsctl add-port s1 eth1')

    cli = CLI(net)
    # After the user exits the CLI, shutdown the network.
    net.stop()


if __name__ == '__main__':
    # This runs if this file is executed directly
    setLogLevel( 'info' )
    runMinimalTopo()

topos = { 'mytopo': MyTopo }