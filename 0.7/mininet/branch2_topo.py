# -*- coding: utf-8 -*-
"""Branch office topology

   terminate switch
      |       |
      hosts  switch
              |
              hosts

    Hosts consist of PC+Phones, ATMs, Security devices (camers, etc)

"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.node import RemoteController, OVSSwitch
from functools import partial

class MyTopo( Topo ):
    "Simple topology example."

    def __init__( self, num ):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        s1 = self.addSwitch( 's1', dpid='%x' % 51)
        s2 = self.addSwitch( 's2', dpid='%x' % 52)
        self.addLink( s1, s2, 2, 1 )

        sec1 = self.addHost( 'sec1', ip='172.16.28.34/27')
        sec2 = self.addHost( 'sec2', ip='172.16.28.35/27')
        self.addLink( sec1, s1, 0, 3 )
        self.addLink( sec2, s1, 0, 4 )

        pnum = 5
        # Add links halv of hosts to one switch - half to another
        for i in range(2, num//2 + 2):
            print('i = ',i, '  pnum = ', pnum)
            h = self.addHost( 'h%s'%i, ip='172.16.129.%s/26'%i)
            t = self.addHost( 't%s'%i, ip='172.16.129.%s/26'%(64+i))
            self.addLink( h, s1, 0, pnum )
            self.addLink( t, s1, 0, pnum+1 )
            pnum += 2
        
        pnum = 4
        for i in range(num//2 + 2, num + 2):
            print('i = ',i, '  pnum = ', pnum)
            h = self.addHost( 'h%s'%i, ip='172.16.129.%s/26'%i)
            t = self.addHost( 't%s'%i, ip='172.16.129.%s/26'%(64+i))
            self.addLink( h, s2, 0, pnum )
            self.addLink( t, s2, 0, pnum+1 )
            pnum += 2


def runMinimalTopo():
    CONTROLLER_IP = '192.168.2.4'
    num = 5
    topo = MyTopo(num) # Create an instance of our topology

    net = Mininet(topo = topo,
        controller=lambda name: RemoteController( name, ip=CONTROLLER_IP),
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        autoSetMacs=True )
    net.start()
    
    for i in range(2, num + 2):
        net.get('h%s'%i).cmd('ip route add default via 172.16.129.1')
        net.get('t%s'%i).cmd('ip route add default via 172.16.129.65')
        
    net.get('sec1').cmd('ip route add default via 172.16.28.33')
    net.get('sec2').cmd('ip route add default via 172.16.28.33')


    #для связи с внешним интерфейсом ВМ
    # ovs возьмет самый первый не используемый порт
    net.get('s1').cmd('ovs-vsctl add-port s1 eth1')

    cli = CLI(net)
    # After the user exits the CLI, shutdown the network.
    net.stop()


if __name__ == '__main__':
    # This runs if this file is executed directly
    setLogLevel( 'info' )
    runMinimalTopo()

topos = { 'mytopo': MyTopo }