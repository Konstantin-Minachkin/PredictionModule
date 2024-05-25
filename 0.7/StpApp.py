# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3 as ofproto
from ryu.ofproto import ofproto_v1_3_parser as parser

from ryu.topology import event
from collections import defaultdict
import helper_methods as util
import ofp_custom_events as c_ev
from config import Config
import random

from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, vlan
from array import array
from copy import copy, deepcopy
import networkx as nx
from networkx.algorithms import tree

# Cisco Reference bandwidth = 1 Gbps
REFERENCE_BW = 10000000

MAX_AREA_NUM = 4096

class StpApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto.OFP_VERSION]

    _CONTEXTS = {
        'net-config': Config
        }
    
    _EVENTS = [ c_ev.PortStateChanged ]


    def __init__(self, *args, **kwargs):
        super(StpApp, self).__init__(*args, **kwargs)
        self.net_config = kwargs['net-config']
        # self.adjacency = defaultdict(dict) #{dp_id:{dp_id:port_num}} общая матрица всех линков
        # self.adjacency не используется, тк в self.areas храняться все те же adj данные о всех линках
        self.areas =  defaultdict( lambda:defaultdict(dict) )  # { area_id : { adjendacy } } отдельная adj область
        self.area_stps = defaultdict( lambda:defaultdict(dict) ) # урезанный adj, хранящий только spanning tree для каждой области
        # self.area_root = {}   # { area-id: root_switch_id } # root делаем не рандомным. Тк считаем, что граф не направленный, то рут не нужен       # Если  область разбилась на несоклько, либо были объединены вместе области - очистить рут
    

    @set_ev_cls(event.EventLinkAdd, MAIN_DISPATCHER)
    def link_add_handler(self, ev):
        s1 = ev.link.src.dpid
        s2 = ev.link.dst.dpid
        p1 = ev.link.src.port_no
        p2 = ev.link.dst.port_no
        ar1 = self.in_area(s1)
        ar2 = self.in_area(s2)
        # print('!!------------------')
        # print('s%s in ar %s   s%s in ar %s'%(s1, ar1, s2, ar2))
        if ar1 is None:
            if ar2 is None:
                # создать новую зону
                ar_id = self.generate_area_id()
                # присоединить к ней линк
                self.add_link(ar_id, s1, s2, p1, p2)
            else:
                # присоединить к зоне ar2
                self.add_link(ar2, s1, s2, p1, p2)
        elif ar2 is None or ar1 == ar2:
            # присоединить к зоне ar1 или # соединить хосты - просто добавить линк в зону
            self.add_link(ar1, s1, s2, p1, p2)
        else:
            # соединить зоны - слить их в одну зону
            # print('self.areas[ar1]')
            # print(self.areas[ar1])
            # print()
            print(self.areas[ar2])
            self.areas[ar1].update(self.areas[ar2])
            # print()
            # print('self.areas[ar1]')
            # print(self.areas[ar1])          
            # добавить линк
            self.add_link(ar1, s1, s2, p1, p2)
            # удалить старую зону - старый ключ
            del self.areas[ar2]
    

    def add_link(self, ar_id, s1_id, s2_id, p1, p2):
        try:
            area = self.areas[ar_id]
            area [s1_id][s2_id] = p1
            area [s2_id][s1_id] = p2
        except KeyError as e:
            # print('Area = ', ar_id)
            print('Key Error STP add_link ', e)
            return 
        # теперь перерасчет stp дерева
        # print('STP tree for ', ar_id, area)
        tree = self.area_stps[ar_id] = self.calculate_stp(area, ar_id)
        # print ('tree = ', tree)
        self.ports_to_stp_tree(tree, area)
        

    def ports_to_stp_tree(self, tree, adj):
        # проверяем линки области. Выставляем state2 на нужных
        for s1 in adj.keys():
            # print('Area key = ', s1, '         ', adj[s1].items())
            for s2, p_num in adj[s1].items():
                if s2 not in tree[s1].keys():    # s1 not in tree_k and 
                    # если связи в дереве нет
                    port = self.net_config.dps[s1].ports[p_num]
                    if port.state == 1:
                        # генерация события об изменении state порта
                        ev = c_ev.PortStateChanged(dp_num = s1, port = port, old_state = port.state)
                        port.state = 2
                        self.send_event_to_observers(ev)
                    #     print('!Link %s-%s is in state 2'%(s1,s2))
                    # print('!Link %s-%s obrabotan'%(s1,s2), port.state)
                else:
                    port = self.net_config.dps[s1].ports[p_num]
                    # убрать линк из state2
                    if port.state == 2:
                        ev = c_ev.PortStateChanged(dp_num = s1, port = port, old_state = port.state)
                        port.state = 1
                        self.send_event_to_observers(ev)
                    #     print('!Link %s-%s is in state 2'%(s1,s2))
                    # print('!Link %s-%s obrabotan'%(s1,s2), port.state)
            # print('\n####  DP PORTS   ####\n')
            # print(self.net_config.dps[s1])
            # print('!-----------------')


    @set_ev_cls(event.EventLinkDelete, MAIN_DISPATCHER)
    def link_delete_handler(self, ev):
        s1 = ev.link.src.dpid
        s2 = ev.link.dst.dpid
        ar1 = self.in_area(s1)
        # ar2 = self.in_area(s2)
        print(f"@@Info: link delete src={s1} dst={s2} area={ar1} ev={ev}")
        # удалить линк из одной области
        # по идее линк - всегда в одной области, поэтому удаляем его только из нее
        if ar1 is None:
            return
        try:
            del self.areas[ar1] [s1][s2]
            del self.areas[ar1] [s2][s1]
        except KeyError as e:
            print('Key Error STP EventLinkDelete ', e)
            # Exception handling if link already deleted
            return
        # Область может стать разъединенной - для определнеия нового числа отдельных областей - пересчитываем все узлы в области и проверяем, осталась ли одна область
        connected_components = self.find_cc(self.areas[ar1])
        # print('len(connected_components) = ', len(connected_components))
        # print('connected_components = \n',connected_components,'\n')
        if len(connected_components) > 1:
            # нужно разъединить области
            for area in connected_components:
                # создать две новые области
                ar_id = self.generate_area_id()
                self.areas[ar_id] = area
                # пересчитать stp для каждой области
                tree = self.area_stps[ar_id] = self.calculate_stp(area, ar_id)
                self.ports_to_stp_tree(tree, area)
            # удалить старую область
            del self.areas[ar1]
        else:
            # просто перерасчитываем stp
            area = connected_components[0]
            tree = self.area_stps[ar1] = self.calculate_stp(area, ar1)
            self.ports_to_stp_tree(tree, area)
        

# methods for work with connected components
# -----

    def in_area(self, s1):
        # ищет, в какой области находится свитч
        for area_id, adj in self.areas.items():
            if s1 in adj.keys():
                return area_id
        return None

    def generate_area_id(self):
        '''  Returns a random area id '''
        # n = random.randint(0, MAX_AREA_NUM)
        n = 1
        # проверка того, есть ли указанный номер группы в сохраненных номерах групп
        all_arid = list(self.areas.keys())
        while n in all_arid:
            n += 1
        return n
    
    def find_cc(self, adjacency):
        # Method to retrieve connected components in an undirected graph
        visited = {}
        cc = []
        #for each node
        for node in adjacency.keys():
            vis = visited.get(node)
            if vis is None:
                temp = {}
                cc.append(self.DFSUtil(adjacency, temp, node, visited))
        return cc

    def DFSUtil(self, adjacency, temp, node, visited):
        # Mark the current vertex as visited
        visited[node] = True
        # Store the vertex to list
        temp[node] = adjacency[node]
        # Repeat for all vertices adjacent to this vertex v
        for neigh in adjacency[node].keys():
            if visited.get(neigh) is None:
                # Update the list
                temp = self.DFSUtil(adjacency, temp, neigh, visited)
        return temp

# -----

    # The main function to construct MST using Kruskal's algorithm
    def calculate_stp(self, adj, ar_id):
        graph = self.adj_to_graph(adj, ar_id)
        edgelist = tree.minimum_spanning_edges(graph, algorithm="boruvka", data=False)
        stp_adj = list(edgelist)
        # print('sorted edgelist')
        # print(sorted(sorted(e) for e in stp_adj))
        # print('stp_adj = \n', stp_adj)
        stp_tree = defaultdict(dict)
        # дерево получилось без весов и ненаправленным - превращаем его в directed
        for edge in stp_adj:
            src = edge[0]
            dst = edge[1]
            # print('SRC, DST = ', src, dst)
            # пишем уже не вес, а порт
            stp_tree[src][dst] = adj[src][dst]
            # связи делаем в обе стороны
            stp_tree[dst][src] = adj[dst][src]
        return stp_tree


    def adj_to_graph(self, adj, ar_id):
        # устанваливаем веса вместо номеров портов и convert граф в Graph
        gr = nx.Graph()
        for src in adj.keys():
            for dst in adj[src].keys():
                w = self.get_link_cost(ar_id, src, dst)
                gr.add_edge(src, dst, weight=w)
        return gr


    def get_link_cost(self, ar_id, s1, s2):
        ''' Get the link cost between two switches  '''
        p1 = self.areas[ar_id][s1][s2]
        p2 = self.areas[ar_id][s2][s1]
        try:
            p1_conf = self.net_config.dps[s1].ports[p1]
            p2_conf = self.net_config.dps[s2].ports[p2]
            #второй вариант, если не те вланы, то делать вес очень большим, но надо тогда еще влан передавать
            pw = REFERENCE_BW/min(p1_conf.speed, p2_conf.speed)
        except KeyError as er:
            print(f"Got exeption {er} \nMaybe there are links in the net, that are not mentioned in configuration?")
            raise KeyError (er)
        return pw
    

    # @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    # def _packet_in_handler(self, ev):
    #     pkt = packet.Packet(array('B', ev.msg.data))
    #     eth_pkt = pkt.get_protocols(ethernet.ethernet)[0]
    #     eth_type = eth_pkt.ethertype
    #     if eth_type != 33024 and eth_type!= 0x800 and eth_type != 0x806:
    #         # ignore not ipv4 or arp packets
    #         return

