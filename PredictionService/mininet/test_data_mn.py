#-*- coding: utf-8 -*-
#script creates mininet net and creates x packets every y*koef timestap

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
from mininet.node import RemoteController, OVSSwitch, OVSController
from functools import partial
# from time import time as t_time, sleep
import datetime
import time 
import random

class MyTopo( Topo ):
    "Simple topology example."

    # def build( self, leaf_sw_am, serv_per_sw, ips):
    #     ts1 = self.addSwitch( 'ts1-1', dpid='%x' % 11)
    #     serv = self.addHost( 'serv1', ip='172.16.24.10/24' )

    def __init__( self, leaf_sw_am, serv_per_sw, ips):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        ts1 = self.addSwitch( 'ts1', dpid='%x' % 11)
        cs1 = self.addSwitch( 'cs1', dpid='%x' % 12)
        # cs2 = self.addSwitch( 'cs2', dpid='%x' % 13)

        self.addLink( ts1, cs1, 2, 1 )
        # self.addLink( ts1, cs2, 3, 1 )

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
            # self.addLink( s, cs2, 2, cs_pnum ) #TODO generate_traffic для некоторых пакетов в сети с двумя путями нормально не работает почему-то
            cs_pnum += 1
            # add servers
            for j in range(1, serv_per_sw+1):
                ip_addr[ip_num] += 1
                serv = self.addHost(f"serv{i}{j}", ip=f"{ips[ip_num]}{ip_addr[ip_num]}/24" )
                self.addLink( serv, s, 0, serv_pnum )
                serv_pnum += 1
            ip_num += 1
            if ip_num >= max_ip_num:
                ip_num = 0


class Packet():
    def __init__(self, **kwargs):
        self.src = kwargs.get('src_name', None)
        self.net = kwargs.get('net')
        self.dst = kwargs.get('dst_name', None)
        self.dst_port = kwargs.get('dst_port', None)
        self.net = kwargs.get('net', None)
        self.ip_src = self.net.get(self.src).IP()
        self.ip_dst = self.net.get(self.dst).IP()
        


def generate_traffic(net, packets, result, packet_ammount = 1, duration=1):
    pnum = 20 #сколько серий пакетов генерировать
    koef = 1 #koef for time difference
    result_file_num = '2'
    random_sec = 11
    result = result+result_file_num+'.log'

    with open(result, 'w') as file:
        file.write("seriesNum;src;dst;ip_src;ip_dst;dst_port;lattency(sec)\n")
        for i in range(0,pnum):
            for p in packets:
                # отправляем каждый пакет из массива packets в сеть
                #-P 1 чтобы сервер стопился сам после того, как один клиент отработает соединение с ним
                # net.get(p.dst).popen(f"iperf -s -p {p.dst_port} -P 1")
                t = time.time()
                print(f"{time.time()} Try to send Packet {p.src} {p.dst}, packet_ammount={packet_ammount}, duration = {duration},")
                # net.get(p.src).cmd(f"iperf -c {p.ip_dst} -p {p.dst_port} -t {duration} ")  #-l 120 -n 5  
                # net.get(p.src).cmd(f"telnet {p.ip_dst} {p.dst_port}")
                net.get(p.src).cmd(f"nc -w 50 {p.ip_dst} {p.dst_port}")
                # net.get(p.src).cmd(f"ping {p.ip_dst} -c 2")
                te = time.time()
                # net.get('serv52').sendInt()
                print(f"{time.time()} Sent Packet difference={te-t} sec")
                file.write(f"{i};{p.src};{p.dst};{p.ip_src};{p.ip_dst};{p.dst_port};{te-t-duration}\n") #duration в показателях lattency не учитываем
                time.sleep(1)  # вoот это тоже надо в send при генерации учитывать 
            if i < (pnum-1):
                print(f"{time.time()} Will sleep")
                time.sleep(koef*(40+random.randint(0,random_sec)))  #random.randint
                print(f"{time.time()} sleeped well")
        print("End of sending data")



def runTestTopo(CONTROLLER_IP, result_url, test = False, dur=1):
    # make net
    gen_time = '2024-05-12 10:53:00.000' #с какого времени начать генерацию пакетов
    gen_time = datetime.datetime.fromisoformat(gen_time)

    leaf_sw_am = 6
    serv_am = 5
    ips = ['172.16.24.', '172.16.0.', '172.16.16.', '172.16.28.', '172.16.40.', '172.16.32.']

    topo = MyTopo(leaf_sw_am, serv_am, ips)

    net = Mininet(topo = topo,
        controller=lambda name: RemoteController( name, ip=CONTROLLER_IP),
        # controller = OVSController,
        switch=partial(OVSSwitch, protocols='OpenFlow13'),
        autoSetMacs=True )

    net.start()

    # make hosts
    ip_num = 0
    max_ip_num = len(ips)
    for i in range(1, leaf_sw_am+1):
        for j in range(1, serv_am+1):
            net.get(f"serv{i}{j}").cmd(f"ip route add default via {ips[ip_num]}1")
        ip_num += 1
        if ip_num >= max_ip_num:
            ip_num = 0

    net.get('ts1').cmd('ovs-vsctl add-port ts1 eth1')

    # run packet ping
    # generate flows
    packets = [  
        Packet(net=net, src_name='serv12', dst_name='serv53', dst_port= 80),
        Packet(net=net, src_name='serv54', dst_name='serv11', dst_port= 80),
        Packet(net=net, src_name='serv21', dst_name='serv63', dst_port= 80),
        Packet(net=net, src_name='serv12', dst_name='serv62', dst_port= 80),
        Packet(net=net, src_name='serv65', dst_name='serv21', dst_port= 80),
        Packet(net=net, src_name='serv32', dst_name='serv35', dst_port= 80),
        Packet(net=net, src_name='serv31', dst_name='serv63', dst_port= 80),
        Packet(net=net, src_name='serv22', dst_name='serv14', dst_port= 80),
        Packet(net=net, src_name='serv55', dst_name='serv15', dst_port= 80),
        Packet(net=net, src_name='serv62', dst_name='serv35', dst_port= 80)
    ]

    if test:
        time.sleep(2)
        while datetime.datetime.now() < gen_time:
            time.sleep(1) #ждем, когда наступит нужное время
        generate_traffic(net, packets, result_url, duration=dur) #отправку всех пакетов из массива в сеть
        time.sleep(2)
    else:
        cli = CLI(net)
    # After the user exits the CLI, shutdown the network.
    net.stop()



if __name__ == '__main__':
    # This runs if this file is executed directly
    setLogLevel( 'info' )
    # runMinimalTopo(CONTROLLER_IP = 'localhost', test_duration = 240)
    runTestTopo(CONTROLLER_IP = 'localhost', result_url = '/home/control/Documents/PredictionService/mininet/Results', test=False, dur=0)


topos = { 'mytopo': ( lambda: MyTopo(6, 5, ['172.16.24.', '172.16.0.', '172.16.16.', '172.16.28.', '172.16.40.', '172.16.32.']) ) }