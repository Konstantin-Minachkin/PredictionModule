# -*- coding: utf-8 -*-
"""HQ office topology.
    Office has tro floors

   _________terminate_switch_____________________
      |       |                       |
             switch-1-floor        switch-2-floor
              |        |            |         |
            switchF1  switchF1    switchF2   switchF2 
               |        |           |           |
              hosts    hosts       hosts       hosts

    Hosts consist of PC+Phones, ATMs, Partners or Security devices (camers, etc)

"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.node import RemoteController, OVSSwitch
from functools import partial

class MyTopo( Topo ):
    "Simple topology example."

    def __init__( self, hosts_per_sw, hnum, tnum, part_per_sw, first_part_num, cnum = 4, anum = 4):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        ts1 = self.addSwitch( 's1', dpid='%x' % 71)
        s1_f1 = self.addSwitch( 's1_f1', dpid='%x' % 72)
        s1_f2 = self.addSwitch( 's1_f2', dpid='%x' % 73)
        s2_f1 = self.addSwitch( 's2_f1', dpid='%x' % 74)
        s3_f1 = self.addSwitch( 's3_f1', dpid='%x' % 75)
        s2_f2 = self.addSwitch( 's2_f2', dpid='%x' % 76)
        s3_f2 = self.addSwitch( 's3_f2', dpid='%x' % 77)

        user_switches = [s1_f1, s2_f1, s3_f1, s1_f2, s2_f2, s3_f2]
        
        self.addLink( ts1, s1_f1, 2, 1 )
        self.addLink( ts1, s1_f2, 3, 1 )

        self.addLink( s1_f1, s2_f1, 2, 1 )
        self.addLink( s1_f1, s3_f1, 3, 1 )

        self.addLink( s1_f2, s2_f2, 2, 1 )
        self.addLink( s1_f2, s3_f2, 3, 1 )

        # ----------- hosts link -----------
        cameras = []
        for i in range(1, cnum+1):
            cameras += self.addHost( 'cam%s'%i, ip='172.16.28.%s/26'%(130+i))
        
        self.addLink( cameras[0], s2_fl1, 0, 2 )
        self.addLink( cameras[1], s3_fl1, 0, 2 )
        self.addLink( cameras[2], s2_fl2, 0, 2 )
        self.addLink( cameras[3], s3_fl2, 0, 2 )

        atms = []
        for i in range(1, anum+1):
            atms += self.addHost( 'atm%s'%i, ip='172.16.28.%s/26'%(130+i))
        
        self.addLink( atms[0], s2_fl1, 0, 3 )
        self.addLink( atms[1], s3_fl1, 0, 3 )
        self.addLink( atms[2], s2_fl2, 0, 3 )
        self.addLink( atms[3], s3_fl2, 0, 3 )
        
        pnum = 4
        for i in range(hosts_per_sw):
            for sw in user_switches:
                h = self.addHost( 'h%s'%hnum, ip='172.16.130.%s/24'%hnum)
                t = self.addHost( 't%s'%tnum, ip='172.16.131.%s/24'%tnum)
                self.addLink( h, sw, 0, pnum )
                self.addLink( t, sw, 0, pnum+1 )
                hnum+=1
                tnum+=1
            pnum += 2
        
        part_num = first_part_num
        for i in range(part_per_sw):
            for sw in user_switches:
                p = self.addHost( 'part%s'%part_num, ip='172.16.41.%s/24'%part_num)
                t = self.addHost( 't%s'%tnum, ip='172.16.131.%s/24'%tnum)
                self.addLink( p, sw, 0, pnum )
                self.addLink( t, sw, 0, pnum+1 )
                part_num+=1
                tnum+=1
            pnum += 2
            

def runMinimalTopo():
    CONTROLLER_IP = '192.168.2.4'
    users_amount = 4
    first_hnum = 10
    first_tnum = 10
    cnum = 4
    anum = 4
    part_num = 2
    first_part_num = 10
    topo = MyTopo(users_amount, first_hnum, first_tnum, part_num, first_part_num, cnum, anum)

    net = Mininet(topo = topo,
        controller=lambda name: RemoteController( name, ip=CONTROLLER_IP),
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        autoSetMacs=True )
    net.start()
    
    for i in range(users_amount):
        net.get('h%s'%first_hnum).cmd('ip route add default via 172.16.130.1')
        net.get('t%s'%first_tnum).cmd('ip route add default via 172.16.131.1')
        hnum+=1
        tnum+=1
        
    for i in range(1, cnum+1):
      net.get('sec%s'%i).cmd('ip route add default via 172.16.28.97')

    for i in range(1, anum+1):
      net.get('atm%s'%i).cmd('ip route add default via 172.16.26.65')

    for i in range(part_num):
      net.get('part%s'%i).cmd('ip route add default via 172.16.41.1')

    net.get('s1').cmd('ovs-vsctl add-port s1 eth1')

    cli = CLI(net)
    # After the user exits the CLI, shutdown the network.
    net.stop()


if __name__ == '__main__':
    # This runs if this file is executed directly
    setLogLevel( 'info' )
    runMinimalTopo()

topos = { 'mytopo': MyTopo }