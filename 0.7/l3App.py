# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3 as ofproto
from ryu.ofproto import ofproto_v1_3_parser as parser
from ryu.lib.ofp_pktinfilter import packet_in_filter, RequiredTypeFilter
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, vlan

from ryu.topology import event
from collections import defaultdict
from array import array
import helper_methods as util
import table
import ofp_custom_events as c_ev
from config import Config
from ipaddress import ip_interface, ip_network, ip_address
import time
import random


from Datatypes.LoggedDps import LoggedDps

PRIORITY_MIN = 0
PRIORITY_DEF = 16000
PRIORITY_MAX = 32000

# Cisco Reference bandwidth = 1 Gbps
REFERENCE_BW = 10000000
DEFAULT_BW = 10000000
MAX_PATHS = 1 #TODO с двумя путями - ошибка на контроллере происходит - пакеты не доходят до контроллера - пути не строятся, но и до хостов пакеты не доходят

DEF_ARP_DEAD_TIME = 3600 #сколько помнить хост в арпе в секундах (dead time)
DEF_QUEUE_DEAD_TIME = 60 * 2
HARD_TIME = 0
IDLE_TIME = 180 #TODO стояло 3600 - для тестов с мининет сделал = 10сек


class L3App(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto.OFP_VERSION]

    _CONTEXTS = {
        'tables': table.Tables,
        'net-config': Config,
        'prediction-dps_with_logflows': LoggedDps
        }

    _EVENTS = [c_ev.NewPredFlows_part2, c_ev.ForPredictionEventOFPPacketIn]
        
    def __init__(self, *args, **kwargs):
        super(L3App, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.net_config = kwargs['net-config']
        self.dps_with_logflows = kwargs['prediction-dps_with_logflows'].dps
        self.cookie = 30
        self.ip_dst_table, self.ip_dstT_id = self.tables.get_table('ip_dst')
        self.flood_table, self.floodT_id = self.tables.get_table('flood')
        self.eth_dst_table, self.eth_dstT_id = self.tables.get_table('eth_dst')
        self.vl_change_table, self.vl_changeT_id = self.tables.get_table('vl_change')
        self.adjacency = defaultdict(dict) #{dp_id:{dp_id:port_num}}
        self.arp_cache = defaultdict() #{string_ip:[ArpRecord]}
        self.pkt_queue = defaultdict(lambda:defaultdict(list)) #{ip.dst:{ip_src:[ [pkt, timeout, src_in_port, src_vl_id] ]}}
        self.group_id = defaultdict(lambda:defaultdict(dict) ) # self.group_id = #{dpid:{gid:{ip: id из pw_lists } } }
        self.pw_lists = defaultdict(list) # {(id, dst_vlan, should_change = False): [ [port_num1, w1], [port_num2, w2] ] } такое разделение нужно, чтобы не хранить по несколько раз листы [ [port_num1, w1], [port_num2, w2] ]
        self.pnum_to_ip = defaultdict(lambda:defaultdict(list)) # { dp_id : {pnum:[ip_dst] } }
        # для ospf

        self.log_table, self.log_table_id = self.tables.get_table('prediction_log')
        self.arp_pred_ips_src = {} 
        self.arp_pred_ips_dst = {}
        self.saved_pred_reqs = defaultdict(lambda:defaultdict(list))
        self.pred_queue_timeout = 10*60 #в секундах

    @set_ev_cls(c_ev.NewDp, CONFIG_DISPATCHER)
    def new_switch_handler(self, ev):
        dp = ev.dp
        #удаляем все прошлые правила
        msgs = self.clean_all_flows(dp)
        #добавляем правило на пересылку ip к контроллеру всего ipv4 траффика
        match = parser.OFPMatch(eth_type=0x0800)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, max_len=256)]
        msgs += [self.make_message (dp, self.ip_dstT_id, PRIORITY_MIN, match, actions = actions)]
        # если траффик запакован во влан, то тоже шлем к контроллеру
        match = parser.OFPMatch(eth_type=33024)
        msgs += [self.make_message (dp, self.ip_dstT_id, PRIORITY_MIN, match, actions = actions)]

        #устанавливаем потоки, которые будут обрабатывать арп запрос о шлюзах по умолчанию
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, max_len=256)]
        for dp_conf in self.net_config.dps.values():
            if dp_conf.ospf_out is not None:
                for ip_adr in dp_conf.announced_gws.values():
                    match = parser.OFPMatch(eth_type = 0x806, arp_tpa = ip_adr.ip)
                    # actions = [parser.OFPActionSetField(vlan_vid = 4096+vl_num)]
                    msgs += [self.make_message (dp, 0, PRIORITY_DEF+10, match, actions = actions)]
                for ip_adr in dp_conf.other_gws.values():
                    match = parser.OFPMatch(eth_type = 0x806, arp_tpa = ip_adr.ip)
                    msgs += [self.make_message (dp, 0, PRIORITY_DEF+10, match, actions = actions)]

        #другие arp запросы шлем дальше
        match = parser.OFPMatch(eth_type=0x0806)
        inst = self.tables.goto_next_of(self.ip_dst_table)
        msgs += [self.make_message (dp, self.ip_dstT_id, PRIORITY_MIN, match, inst)]
        # любой другой траффик будет отброшен

        # добавляем правила для работы с таблцией prediction_log'
        msgs += self.add_prediction_table_def_flows(dp)

        #отправляем все сообщения
        util.send_msgs(dp, msgs)


    @set_ev_cls(event.EventLinkAdd, MAIN_DISPATCHER)
    def link_add_handler(self, ev):
        s1 = ev.link.src
        s2 = ev.link.dst
        self.adjacency[s1.dpid][s2.dpid] = s1.port_no
        self.adjacency[s2.dpid][s1.dpid] = s2.port_no
        # тк порт новый и за ним никаких айпи не светится, то clean_ip_flows не применяется
        # TODO А если путь через этот порт будет лучше?


    @set_ev_cls(event.EventLinkDelete, MAIN_DISPATCHER)
    def link_delete_handler(self, ev):
        s1 = ev.link.src
        s2 = ev.link.dst
        
        # Exception handling if switch already deleted
        try:
            p1_num = self.adjacency[s1.dpid][s2.dpid]
            p2_num = self.adjacency[s2.dpid][s1.dpid]
            del self.adjacency[s1.dpid][s2.dpid]
            del self.adjacency[s2.dpid][s1.dpid]
            #удалять потоки, заявязанные на связях
            self.clean_ip_flows(s1.dpid, p1_num)
            self.clean_ip_flows(s2.dpid, p2_num)
        except KeyError:
            pass


    @set_ev_cls(c_ev.NewPortNativeVlan, MAIN_DISPATCHER)
    @set_ev_cls(c_ev.NewPortTaggedVlan, MAIN_DISPATCHER)
    @set_ev_cls(c_ev.PortNeedClean, MAIN_DISPATCHER)
    def _port_changed_vlan_handler(self, ev):
        p = ev.port.num
        dp = ev.dp.id
        self.clean_ip_flows(dp, p)

    
    @set_ev_cls(c_ev.DelBorderRouter, MAIN_DISPATCHER)
    def _border_state_changed_handler(self, ev):
        old_dpc = ev.old_dp_conf
        new_dpc = ev.dp_conf
        dp = new_dpc.dp_obj
        self.clean_ip_flows(dp, old_dpc.ospf_out)


    @set_ev_cls(c_ev.NewBorderRouter, MAIN_DISPATCHER)
    def _border_state_changed_handler(self, ev):
        old_dpc = ev.old_dp_conf
        new_dpc = ev.dp_conf
        # установить влан на прошлый порт, удалить влан с настоящего бордер свитча
        if old_dpc is not None and old_dpc.ospf_out is not None:
            # удалить потоки для старого порта
            self.clean_ip_flows(old_dpc.id, old_dpc.ospf_out)
        # if new_dpc.ospf_out is not None: #всегда сработает
        # удалить потоки для порта, который стал ospf_out
        self.clean_ip_flows(new_dpc.id, new_dpc.ospf_out)


    @set_ev_cls(c_ev.VlRouteChange, MAIN_DISPATCHER)
    def _vl_route_changed_handler(self, ev):
        # и при изменении рут вланов, и при удалении рут вланов, чистим потоки л3 таблицы основываясь на номерах вланов
        # TODO TEST
        # r_id = ev.rid
        old_route = ev.old_route
        print('!!! VlRouteChange route vlans = ', old_route)
        msgs = []
        for dp in self.net_config.active_dps.values():
            dp = dp.dp_obj
            for vl in old_route:
                vid = self.net_config.vlans[vl].vid
                print('vid = ', vid)
                msgs += [self.del_flow(dp, self.ip_dstT_id, match = parser.OFPMatch(eth_type=0x0800, metadata = vid*1000))]
        msgs += util.barrier_request(dp)
        util.send_msgs(dp, msgs)

    
    @set_ev_cls(c_ev.VlRouteDelete, MAIN_DISPATCHER)
    def _vl_route_del_handler(self, ev):
        # TODO TEST
        # r_id = ev.rid
        route = ev.route
        print('!!! VlRouteDelete route vlans = ', route)
        msgs = []
        for dp in self.net_config.active_dps.values():
            dp = dp.dp_obj
            for vl in route:
                vid = self.net_config.vlans[vl].vid
                print('vid = ', vid)
                msgs += [self.del_flow(dp, self.ip_dstT_id, match = parser.OFPMatch(eth_type=0x0800, metadata = vid*1000))]
        msgs += util.barrier_request(dp)
        util.send_msgs(dp, msgs)
        

    # @set_ev_cls(c_ev.VlRouteNew, MAIN_DISPATCHER)
    # def _vl_route_add_handler(self, ev):
    #     # ничего не делаем, тк никаких потоков для общения между старыми вланами и новым установлено не было


    # @set_ev_cls(c_ev.NeighMacChanged, CONFIG_DISPATCHER)
    # def _new_mac_handler(self, ev):
    #     # TODO если нейборов больше одного, надо еще разграничивать по ip
    #     self.neigh_mac[ev.dp_id] = ev.mac


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        pkt = ev.msg.data
        unserialezed_pkt = packet.Packet(array('B', pkt))
        eth_pkt = unserialezed_pkt.get_protocols(ethernet.ethernet)[0]
        eth_type = eth_pkt.ethertype 
        if eth_type != 33024 and eth_type!= 0x800 and eth_type != 0x806:
            # ignore not ipv4 or arp packets
            return
        
        # self.send_event_to_observers( c_ev.ForPredictionEventOFPPacketIn(event=ev) )

        if ev.msg.reason is ofproto.OFPR_ACTION and ev.msg.table_id == self.log_table_id:
            # ignore log packets
            return

        #обрабатываем ethertype запакованный во влан пакеты
        vlan_pkt = unserialezed_pkt.get_protocols(vlan.vlan)
        vlan_id = None
        if bool(vlan_pkt):
            vlan_pkt = vlan_pkt[0]
            eth_type = vlan_pkt.ethertype
            if eth_type!= 0x800 and eth_type != 0x806:
                # ignore not ipv4 or arp packets
                return
            vlan_id = vlan_pkt.vid

        dp = ev.msg.datapath
        dp_conf = self.net_config.dps[dp.id]
        in_port = ev.msg.match['in_port']
        arp_pkt = unserialezed_pkt.get_protocols(arp.arp)
        
        # print(f'!!!!for dp {dp.id} in_port={in_port} ev.msg.table_id={ev.msg.table_id} ev.msg.reason={ev.msg.reason} ev.msg.cookie={ev.msg.cookie} packet == ', unserialezed_pkt)
        
        msgs = []
        if len(arp_pkt) > 0:
            # если арп пришел от внешнего роутера - все так же отвечаем на него здесь, а не в ospf app
            # print('!!!! ARP packet == ', unserialezed_pkt)
            arp_pkt = arp_pkt[0]
            #добавляем ip в арп таблицу
            learn_m = self.learn_host(dp.id, arp_pkt.src_ip, in_port, arp_pkt.src_mac)
            if learn_m is not None:
                msgs += learn_m
            if arp_pkt.opcode == 1:
                # print('00  ARP reply', arp_pkt.dst_ip)
                if self.is_gateway(arp_pkt.dst_ip):
                    gw_mac = self.get_mac_of_gw(arp_pkt.dst_ip)
                    if gw_mac is None:
                        print('gw mac is None')
                        return
                    msgs += util.arp_reply(src_mac = gw_mac, src_ip = arp_pkt.dst_ip, dst_mac = arp_pkt.src_mac, dst_ip = arp_pkt.src_ip, dp=dp, out_ports=[in_port]) #тут отправляем без влана, тк попадать будет в контроллер сразу с первого свитча
            else:
                # print('Got arp opcode = 2')
                # self.check_queue_time() #delete all old queues
                # msgs += util.barrier_request(dp)
                # dst = self.pkt_queue.get(arp_pkt.src_ip)
                # TODO надо еще dp_src охранять иначе глючно работает для обращений ко внешним хостам, пока отключил
                # if dst is not None:
                #     for src, src_lists in dst.items():
                #         #послать все пакеты из очереди к dst от всех src
                #         for s_list in src_lists:
                #             print('Izbavlyaemsya ot ocheredi', src, arp_pkt.src_ip, eth_pkt.dst, vlan_id)
                            # print(in_port, src, arp_pkt.src_ip, vlan_id)
                            # print(s_list[0], s_list[2])
                            # TODO надо еще dp_src охранять иначе глючно работает, пока отключил
                            # self.send_pkt(s_list[2], src, arp_pkt.src_ip, eth_pkt.dst, s_list[3], s_list[0], dst_vid = vlan_id) #TODO test if it really sends packets and host is getting them #dpid_src = dp.id

                # проверяем, пришел ли арп запрос для instal_predictions запросы, про который мы что-то не знаем - если да, то выполнить его еще раз
                print(f'@self.check_prediction_queue({arp_pkt.src_ip})')
                self.check_prediction_queue(arp_pkt.src_ip)

        else:
            ip_pkt = unserialezed_pkt.get_protocols(ipv4.ipv4)[0]
            if ip_pkt.proto == 89:
                # ospf will hold this types of packets
                return
            fromBorder = False
            fromInternet = False
            toInternet = False
            # проверяем, пришел ли пакет на бордер роутер от внешней сети
            if in_port == dp_conf.ospf_out and dp_conf.ospf_out is not None:
                # хост пришел из внешних источников
                fromBorder = True

            if not fromBorder:
                #добавляем ip в арп таблицу
                learn_m = self.learn_host(dp.id, ip_pkt.src, in_port, eth_pkt.src)
                if learn_m is not None:
                    msgs += learn_m
                # else:
                #     print('learn_m is None and packet is not from ospf_out port')
                #     return
            
            #если айпи - адрес шлюза по умолчанию, тогда ничего не делать
            if self.net_config.is_gwip(ip_pkt.dst):
                # TODO вернутб пинг от шлюза по умолчанию
                return
            
            #проверяем, принадлежат ли ip_src и ip_dst сеткам на одном и том же граничном роутере
            src_brd_dp, src_brd_mac = self.get_bord_swid(ip_pkt.src)
            # if fromBorder:
            if src_brd_dp == -1:
                # если сработал - значит пришел пакет из неизвестной сети
                print('src_brd_dp of dst == -1 and fromInternet is True for src = ', ip_pkt.src, 'dst = ', ip_pkt.dst)
                fromInternet = True

            brd_dp, *tmp = self.get_bord_swid(ip_pkt.dst)
            if brd_dp == -1:
                print('dst_brd_dp of dst == -1 src = ', ip_pkt.src, 'dst = ', ip_pkt.dst)
                # если сработал - значит мы из сети шлем пакет неизвестно куда, надо переслать на vedge 
                toInternet = True

            if src_brd_dp == brd_dp or fromBorder: #fromInternet
                # пакет надо передать в эту сеть к назначению
                #ищем айпишники за какими dp они находятся, также передаем найденный gw(адрес шлюза по умолчанию из той же сети) для этого dst
                dst_dp, out_port, *tmp = self.find_ipdp(ip_pkt.dst) #*tmp - чтобы все остальные аргументы, которые может вернуть функция не учитывались
                if dst_dp is not None:
                    #если ip в арп таблицах найден
                    #print(f'Found ip in arp table: src_brd_dp={src_brd_dp}, brd_dp={brd_dp}, dst_dp={dst_dp}, fromBorder={fromBorder}')
                    # if not fromInternet:
                        # # строим путь до хоста в локальной сети
                        # self.send_pkt(in_port, ip_pkt.src, ip_pkt.dst, eth_pkt.dst, vlan_id, pkt = pkt, dpid_src = dp.id, dpid_dst = dst_dp, dst_port = out_port, brd_dp=brd_dp)
                    # else:
                    # обработка ситуации, когда хост источника в интернете
                    self.send_pkt(in_port, ip_pkt.src, ip_pkt.dst, eth_pkt.dst, vlan_id, pkt = pkt, dpid_src = dp.id, dpid_dst = dst_dp, dst_port = out_port, brd_dp=brd_dp, src_mac = eth_pkt.src)
                #если адрес назначения не нашли - нужно отправить арп запрос
                else:
                    #print('Dont know dst_dp')
                    #Ищем mac и айпи шлюза по умолчанию для этого айпи
                    gw_mac, gw_ip = self.get_gw_mac_ip_for_ip(brd_dp, ip_pkt.dst)
                                            
                    # блокируем на время остальные запросы
                    match = parser.OFPMatch(eth_type = 0x800, ipv4_src = ip_pkt.src, ipv4_dst = ip_pkt.dst)
                    msgs = [self.make_message (dp, self.ip_dstT_id, PRIORITY_MAX, match, idle_timeout=1, hard_timeout=1)]
                    util.send_msgs(dp, msgs)

                    if gw_mac is None:
                        return
                    #сохраняем пакет
                    # TODO если шлем через бордер роутер, то надо еще где-то это сохранять, иначе send_pkt будет пытаться тсроить пути через роутеры из разных сетей, для которых нет путей
                    self.pkt_queue[ip_pkt.dst][ip_pkt.src].append([pkt, time.time(), in_port, vlan_id])
                    # шлем арп запрос во вланы
                    msgs += self.send_arp(dp, mac_src = gw_mac, ip_src = gw_ip, ip_dst = ip_pkt.dst, src_vlan_id = vlan_id, brd_dp = src_brd_dp, fromBorder = fromBorder)
            else:
                #если нет, строим путь до шлюза по умолчанию, все пакеты передаем туда
                #ставим потоки чтобы из src достичь шлюза по умолчанию и отправить пакет дальше
                print(dp.id, ' Test 1 brd_dp= ', brd_dp, '  src_brd_dp = ', src_brd_dp, toInternet, fromInternet, ' fromBorder = ', fromBorder)
                if not fromInternet:
                    brd_dp = src_brd_dp
                # if not fromBorder:
                #     print('To internet')
                #     brd_dp = src_brd_dp
                #     out_port = self.net_config.dps[src_brd_dp].get_ospf_port()
                out_port = self.net_config.dps[brd_dp].get_ospf_port()
                self.send_pkt(in_port, ip_pkt.src, ip_pkt.dst, eth_pkt.dst, vlan_id, pkt, dpid_src = dp.id, dpid_dst = brd_dp, dst_port=out_port, brd_dp=brd_dp, brd_mac = src_brd_mac, toInternet = toInternet, src_mac = eth_pkt.src) #шлем пакет на dst=border, после бордер будет сам в оспф пересылать дальше
                    
        # print('!!Ending of pcaket in\nArp cahce = ')
        # for a, b in self.arp_cache.items():
        #     print (a, str(b))    
        # print('Packet in end of ending\n')

        # print ("!!!!!!!!!!!! L3 msgs")
        # for msg in msgs:
        #     print(dp.id, msg)
        #     print()
        # print('End of send')
        util.send_msgs(dp, msgs)

    def send_arp(self, dp, mac_src, ip_src, ip_dst, src_vlan_id, brd_dp, fromBorder = False):
        #шлем арп запрос в порты, основываясь на вланах, 
        # Ищем, в каком влане находится адрес назначения 
        dst_vlan_id = self._get_vlan(ip_dst)
        # print('@@@ ', dst_vlan_id, mac_src, ip_src, ip_dst, src_vlan_id, brd_dp, fromBorder)
        if dst_vlan_id is None:
            # не знаем куда слать, шлем на шлюз по умолчнаию
            print('dst_vlan_id is None')
            return []
        
        if src_vlan_id == dst_vlan_id:
            # если общаемся в пределах одного влана
            dp_ports = self.net_config.dps[dp.id].ports
            p_vid = src_vlan_id
            # dp_ports[in_port].native_vlan #по идее порт всегда аксесный, поэтому только нэйтив влан смотрим

            tag_actions = []
            native_actions = []
            #шлем через все порты того же влана, и в in порт тоже
            for p in dp_ports.values():
                #если не нужно слать в in_port то вот это
                # p_num = dp_ports[in_port].num
                # if p.num == p_num:
                #     continue
                if p.tagged_vlans is not None:
                    if p_vid in p.tagged_vlans and p.state != 2:
                        tag_actions+= [parser.OFPActionOutput(p.num)]
                elif p.native_vlan == p_vid and p.state != 2: #and p.native_vlan is not None
                    native_actions+= [parser.OFPActionOutput(p.num)]

            msgs = []
            if len(native_actions) > 0:
                #шлем арп запрос без влана, в аксессные порты
                arp_req = util.arp_request(src_mac = mac_src, src_ip = ip_src, dst_ip = ip_dst)
                msgs+=[parser.OFPPacketOut(datapath=dp, buffer_id=0xffffffff, in_port=ofproto.OFPP_CONTROLLER, actions=native_actions, data=arp_req)]

            if len(tag_actions) > 0:
                # в тегированные шлем со вланом == влану запросившего
                arp_req = util.arp_request(src_mac = mac_src, src_ip = ip_src, dst_ip = ip_dst, vid = src_vlan_id)
                msgs+=[parser.OFPPacketOut(datapath=dp, buffer_id=0xffffffff, in_port=ofproto.OFPP_CONTROLLER, actions=tag_actions, data=arp_req)]
            

        else:
            # если общаемся между вланами
            # ищем имена вланов
            print('between vlans   ', src_vlan_id, dst_vlan_id, ip_src, ip_dst)
            vl_src_name = None
            vl_dst_name = None
            for vl in self.net_config.vlans.values():
                if vl_src_name is None and vl.vid == src_vlan_id:
                    vl_src_name = vl.name
                elif vl_dst_name is None and vl.vid == dst_vlan_id:
                    vl_dst_name = vl.name
                if vl_dst_name is not None and vl_src_name is not None:
                    break

            # проверяем, разрешено ли общаться этим вланам
            def _check_vlan(self, vl_src_name, vl_dst_name):
                for vl_route in self.net_config.route_vlans.values():
                    if vl_src_name in vl_route and vl_dst_name in vl_route:
                        return True
                return False
                
            if not _check_vlan(self, vl_src_name, vl_dst_name):
                # если нет разрешение на общение между вланами - ставим поток для блокировки дальнейшего общения со всей сетью для этого влана
                net = self.get_net_of_ip(brd_dp, ip_dst)
                match = parser.OFPMatch(eth_type = 0x800, metadata = src_vlan_id*1000, ipv4_dst = net)
                msgs = [self.make_message (dp, self.ip_dstT_id, PRIORITY_MAX, match)]
                util.send_msgs(dp, msgs)
                return msgs

            # если нет
            # ставим поток, на время блокирующий арпы к этому же хосту
            match = parser.OFPMatch(eth_type = 0x806, arp_op = 1, arp_spa = ip_src, arp_tpa = ip_dst)
            msgs = [self.make_message (dp, self.ip_dstT_id, PRIORITY_MAX, match, idle_timeout=3, hard_timeout=3)]
            util.send_msgs(dp, msgs)

            for temp_dp in self.net_config.active_dps.values():
                # на все dp ставим потоки с метадатой для изменения вланов
                temp_dp = temp_dp.dp_obj
                match = parser.OFPMatch(eth_type = 0x806, arp_op = 1, arp_spa = ip_src, arp_tpa = ip_dst)
                inst = [parser.OFPInstructionWriteMetadata(metadata = dst_vlan_id * 1000, metadata_mask = 0xFFFFFFFF)]
                inst += self.tables.goto_next_of(self.eth_dst_table)
                self.send_message(temp_dp, self.ip_dstT_id, PRIORITY_DEF+10, match, inst, idle_timeout=10, hard_timeout=10)

                # ставим поток в обратную стороу для получения ответа
                match = parser.OFPMatch(eth_type = 0x806, arp_op = 2, arp_tpa = ip_src, arp_spa = ip_dst)
                inst = [parser.OFPInstructionWriteMetadata(metadata = src_vlan_id * 1000, metadata_mask = 0xFFFFFFFF)]
                inst += self.tables.goto_next_of(self.eth_dst_table)
                self.send_message(temp_dp, self.ip_dstT_id, PRIORITY_DEF+10, match, inst, idle_timeout=10, hard_timeout=10)

            # формируем арп запрос с правильными вланами
            dp_ports = self.net_config.dps[dp.id].ports
            dst_tag_actions = []
            native_actions = []
            #шлем через все порты того же влана, и в in порт тоже
            for p in dp_ports.values():
                if p.tagged_vlans is not None:
                    print(p.num, vl_dst_name, p.tagged_vlans, p.state)
                    if vl_dst_name in p.tagged_vlans and p.state != 2:
                        dst_tag_actions+= [parser.OFPActionOutput(p.num)]
                elif p.native_vlan == vl_dst_name and p.state != 2: #and p.native_vlan is not None
                    native_actions+= [parser.OFPActionOutput(p.num)]

            msgs = []
            if len(native_actions) > 0:
                #шлем арп запрос без влана, в аксессные порты
                arp_req = util.arp_request(src_mac = mac_src, src_ip = ip_src, dst_ip = ip_dst)
                msgs+=[parser.OFPPacketOut(datapath=dp, buffer_id=0xffffffff, in_port=ofproto.OFPP_CONTROLLER, actions=native_actions, data=arp_req)]

            if len(dst_tag_actions) > 0:
                # в тегированные шлем со вланом == dst_vlan
                arp_req = util.arp_request(src_mac = mac_src, src_ip = ip_src, dst_ip = ip_dst, vid = dst_vlan_id)
                msgs+=[parser.OFPPacketOut(datapath=dp, buffer_id=0xffffffff, in_port=ofproto.OFPP_CONTROLLER, actions=dst_tag_actions, data=arp_req)]
        
        # print('!arp msgs = ')
        # for m in msgs:
        #     print(m,'\n')
        return msgs


    def send_pkt(self, src_port, ip_src, ip_dst, mac_dst, src_vid, pkt = None, dpid_src = None, dpid_dst = None, dst_port = None, brd_dp = None, dst_vid = None, brd_mac = None, toInternet = False, src_mac = None):
        #определяем наилучший путь, инсталим его, шлем пакет через один из портов, инсталим потоки для дальнейшей обработки
        if dpid_src is None:
            #найти src
            dpid_src, *tmp  = self.find_ipdp(ip_src)
        if dpid_dst is None:
            #найти dst
            print('dpid_dst is None ')
            dpid_dst, dst_port, *tmp  = self.find_ipdp(ip_dst)
        elif dst_port is None:
            print('Send_pkt error: dp_id dst and dst_port should be provided together')
            return 

        if dst_vid is None:
            # проверяем, какой влан у назначения
            dst_vid_name = self.net_config.dps[dpid_dst].ports[dst_port].native_vlan
            dst_vid = self.net_config.vlans[dst_vid_name].vid
        else:
            dst_vid = src_vid

        if dpid_src is None:
            # возникает, если в очереди есть пакеты, идующие в Интрнет TODO для исправления надо в queue сохранять еще dp_src
            return

        dp = self.net_config.dps[dpid_src].dp_obj
        
        if src_vid == dst_vid:
            paths = self.get_optimal_paths(dpid_src, dpid_dst, src_vid)
        else:
            paths, brd_vl_dps = self.get_inter_vl_optimal_paths(dpid_src, dpid_dst, src_vid, dst_vid, dst_port)
        
        if not bool(paths):
            #если путей нет, 
            print(f'!No paths are found: if not bool(paths)==True for: dpid_src={dpid_src}, dpid_dst={dpid_dst}')
            # то инсталим потоки, которые будут такие пакеты отбрасывать, время жизни потока - короткое
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst)
            self.send_message(dp, self.ip_dstT_id, PRIORITY_DEF, match, idle_timeout=30, hard_timeout=30)
            # передаем на бордер dp?
            # dpid_dst = brd_dp
            return 

        if src_vid == dst_vid:
            self.install_paths(dpid_src, src_port, dpid_dst, dst_port, mac_dst, ip_dst, src_vid, paths, brd_dp, mac_src=src_mac)
        else:
            self.install_paths(dpid_src, src_port, dpid_dst, dst_port, mac_dst, ip_dst, src_vid, paths, mac_src=src_mac, brd_dp = brd_dp, dst_vlan_id = dst_vid, vl_brd_dps = brd_vl_dps)
                
        if pkt is not None:
            # шлем пакет через порт последнего коммутатора
            dp = self.net_config.dps[dpid_dst].dp_obj #не нужно, тк уже выше есть
            dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=ofproto.OFP_NO_BUFFER, in_port=ofproto.OFPP_CONTROLLER, actions=[parser.OFPActionOutput(dst_port)], data=pkt))


    @set_ev_cls(c_ev.NewPredFlows, MAIN_DISPATCHER)
    def _install_predict_flows(self, ev):
        self.install_predictions(ev)


    def add_prediction_table_def_flows(self, dp_obj):
        # работаем с лог таблицей
        msgs = []
        # Записи из таблц не очищаем
        # del_msg = parser.OFPFlowMod(datapath=dp_obj, table_id=self.log_table_id, command=ofproto.OFPFC_DELETE, out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY, match = None)
        # msgs.append(del_msg)
        # msgs.append(util.barrier_request(dp_obj))

        # добавляем запись пропускать все пакеты
        inst = self.tables.goto_next_of(self.log_table)
        last_msg = parser.OFPFlowMod(datapath=dp_obj, table_id=self.log_table_id, match=parser.OFPMatch(), instructions=inst, command = ofproto.OFPFC_ADD, idle_timeout = 0, hard_timeout = 0, priority=PRIORITY_MIN)
        
        msgs.append(last_msg)
        return msgs


    #найти src и dst
    def find_ip(self, ip):
        record = self.arp_cache.get(ip)
        print(f"found_ip arp record for {ip} = {record}")
        if record is not None:
            return record
        else:
            return None


    def _clean_old_pred_queue(self):
        #clean old (more than self.pred_queue_timeout seconds) entries from self.saved_pred_reqs
        cur_time = time.time()
        for dst, svals in list(self.saved_pred_reqs.items()):
            for src, vals in svals.items():
                i = -1
                for v in vals:
                    i+=1
                    if cur_time > v[1]+self.pred_queue_timeout:
                        self.saved_pred_reqs[dst][src].pop(i)
                        if not self.saved_pred_reqs[dst][src]:
                            # if list become empty
                            self.saved_pred_reqs[dst].pop(src)
        

    def check_prediction_queue(self, arp_src_ip):
        #удалить очередь по таймауту - таймаут долгий будет - но не бесконечный, чтобы пакеты в очереди не копились
        self._clean_old_pred_queue()
        # проверить, есть ли в очереди пакет, где этот ip был неизветсен
        ready_events = []
        dn_dst = self.arp_pred_ips_dst.get(arp_src_ip, False)
        dn_src = self.arp_pred_ips_src.get(arp_src_ip, False)
        if dn_dst:
            # удалить ip из очереди
            self.arp_pred_ips_dst.pop(arp_src_ip)

            # сначала ищем те места, где пакет = dst
            for src in self.saved_pred_reqs[arp_src_ip].keys():
                # знаем ли ip_src для него?
                dn = self.arp_pred_ips_src.get(src, False)
                if not dn:
                    #если знаем - те его нету в словаре айпишников которые не знаем
                    # сохраняем dst и src ip - дальше мы их удалим из очереди евентов и отправим евент
                    ready_events.append([arp_src_ip, src])

        if dn_src:
            # теперь аналогично для евентов, где пакет = src
            # удалить ip из очереди
            self.arp_pred_ips_src.pop(arp_src_ip)

            for dst, vals in self.saved_pred_reqs.items():
                # ищем где этот пакет = src
                for src in vals:
                    if src==arp_src_ip:
                        # знаем ли ip_dst для него?
                        dn = self.arp_pred_ips_dst.get(dst, False)
                        if not dn:
                            #если знаем - те его нету в словаре айпишников которые не знаем
                            # сохраняем dst и src ip - дальше мы их удалим из очереди евентов и отправим евент
                            ready_events.append([dst, arp_src_ip])
        
        # по всем полям, где ip уже известны - отправить install_predictions(ev) по всем евентам
        for r in ready_events:
            sev = self.saved_pred_reqs[r[0]][r[1]]
            for e in sev:
                # print(f"self.install_predictions({e}) | self.saved_pred_reqs[r[0]][r[1]]={self.saved_pred_reqs[r[0]][r[1]]}")
                self.install_predictions(e[0])
        # иначе ничего

    
    def _find_first_native_vlan_sw(self, brd_dp_id, vl_id, parent=None):
        # find first sw with native vlan port. Sw is connected with brd_dp (may be connected through other switches)
        found = None
        vname = None
        for dp_id, con_port in self.adjacency[brd_dp_id].items():
            if parent is not None and parent==dp_id:
                continue
            dports = self.net_config.dps[dp_id].ports
            for p in dports.values():
                if p.native_vlan is not None:
                    # TODO вот это костыль 
                    vid = self.net_config.vlans[p.native_vlan].vid
                    if vid != vl_id:
                    # /конец констыля
                        # нашли свитч, который связан с бордер свитч и имеет нативный влан - те к нему хост подключен
                        return dp_id, vid
            if found is None:
                found, vname = self._find_first_native_vlan_sw(dp_id, vl_id, brd_dp_id)
                if found is not None:
                    return found, vname
        return found, vname


    def generate_arp(self, ip, brd_dp_id):
        # кинуть арп запрос
        gw_mac, gw_ip = self.get_gw_mac_ip_for_ip(brd_dp_id, ip)
        brd_dp = self.net_config.dps[brd_dp_id]
        if gw_mac is None:
            return True
        # шлем арп запрос во вланы
        
        # TODO в _find_first_native_vlan_sw сделал костыль - vlan ищем тот который не совпадает с вланом сети
        # сделал так из-за того, что ничего не работает, когда вланы совпадают
        # если поправить работу send_arp при одинаковых vlanax - можно будет эти три строчки раскоментить и избавиться от вызова алгоритма _find_first_native_vlan_sw
        # dp_id = brd_dp_id
        # vlan_id = self._get_vlan(gw_ip)
        # dp = brd_dp.dp_obj
        dp_id, vlan_id = self._find_first_native_vlan_sw(brd_dp_id, self._get_vlan(ip))
        print(f'generate_arp({ip}) _find_first_native_vlan_sw found dpid = {dp_id} | vlan_id={vlan_id}')
        dp = self.net_config.dps[dp_id].dp_obj
        msgs = self.send_arp(dp, mac_src = gw_mac, ip_src = gw_ip, ip_dst = ip, src_vlan_id = vlan_id, brd_dp = brd_dp)
        util.send_msgs(dp, msgs)
        return True


    def install_predictions(self, ev):
        packet = ev.predicted_packet
        cookie = ev.cookie

        src_brd_dp_id, src_brd_mac = self.get_bord_swid(packet.ip_src)
        dst_brd_dp_id, *tmp = self.get_bord_swid(packet.ip_dst)

        if src_brd_dp_id == -1 and dst_brd_dp_id == -1:
            print("Error L3App: install_predictions no src_brd_dp_id and dst_brd_dp_id")
            return

        src_record = self.find_ip(packet.ip_src)
        # TODO а если dpid = None, тк пакет идет из\в инетрнет? что тогда?
        # TODO а если в памяти контроллера просто не окажется этих пакетов (он их забудет)? Что тогда? Как их искать?
        dst_record = self.find_ip(packet.ip_dst)
   
        # обработка ситуации того, что контроллер не знает в своем кэше про эти айпи
        src_arp = False
        dst_arp = False
        if src_record is None and src_brd_dp_id != -1:
            src_arp = self.generate_arp(packet.ip_src, src_brd_dp_id)
        if dst_record is None and dst_brd_dp_id != -1:
            dst_arp = self.generate_arp(packet.ip_dst, dst_brd_dp_id)

        if src_arp or dst_arp:
            # если не знаем какой-то из айпи, то сохраняем запрос - выполним его тогда, когда получим инфу про ip
            # TODO возможно оно не сработает, если ip из внешней сети
            if src_arp:
                # если src неизветсен - сохранить эту инфу
                self.arp_pred_ips_src[packet.ip_src] = True
            if dst_arp:
                # если dst неизветсен - сохранить эту инфу
                self.arp_pred_ips_dst[packet.ip_dst] = True
            # сохранить сам запрос в очереди
            self.saved_pred_reqs[packet.ip_dst][packet.ip_src].append([ev, time.time()])
            return
        #/обработка  

        dpid_src = src_record.dpid
        src_port_num  = src_record.port

        dpid_dst = dst_record.dpid
        dst_port_num  = dst_record.port

        if src_brd_dp_id == -1:
            # если сработал - значит пришел пакет из неизвестной сети fromInternet
            src_brd_dp_id = dst_brd_dp_id
            dpid_src = src_brd_dp_id
            src_port_num = self.net_config.dps[src_brd_dp_id].get_ospf_port()

        if dst_brd_dp_id == -1:
            # если сработал - значит мы из сети шлем пакет неизвестно куда, надо переслать на vedge 
            #  строим путь до шлюза по умолчанию, все пакеты передаем туда
            dst_brd_dp_id = src_brd_dp_id
            dpid_dst = src_brd_dp_id
            dst_port_num = self.net_config.dps[dst_brd_dp_id].get_ospf_port()
            
        # ищем инфу по вланам
        dst_vid_name = self.net_config.dps[dpid_dst].ports[dst_port_num].native_vlan
        dst_vid = self.net_config.vlans[dst_vid_name].vid

        src_vid_name = self.net_config.dps[dpid_src].ports[src_port_num].native_vlan
        src_vid = self.net_config.vlans[src_vid_name].vid

        # TODO нет реализации того, если ip_dst mac = None и он с инета пришел, тк 
        # айпи в Интернете - тогда по идее сам vEdge будет менять ip_mac на нужный
        # dst_mac = self.get_mac_of_ip(packet.ip_dst)

        if src_vid == dst_vid:
            paths = self.get_optimal_paths(dpid_src, dpid_dst, src_vid)
            brd_vl_dps = None
        else:
            paths, brd_vl_dps = self.get_inter_vl_optimal_paths(dpid_src, dpid_dst, src_vid, dst_vid, dst_port_num)

        if not bool(paths):
            #если путей нет, 
            print('!not bool(paths) = ', dpid_src, dpid_dst)
            # то инсталим потоки, которые будут такие пакеты отбрасывать, время жизни потока - короткое
            dp_src = self.net_config.dps[dpid_src].dp_obj
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=packet.ip_dst)
            self.send_message(dp_src, self.ip_dstT_id, PRIORITY_DEF, match, idle_timeout=30, hard_timeout=30)
            return 

        # install paths for routing
        self.install_paths(dpid_src = dpid_src, first_port = src_port_num, dpid_dst = dpid_dst, last_port = dst_port_num, mac_src = src_record.mac, mac_dst = dst_record.mac, ip_dst = packet.ip_dst, src_vlan_id = src_vid, paths = paths, brd_dp = dst_brd_dp_id, dst_vlan_id = dst_vid, vl_brd_dps = brd_vl_dps)

        # install_log_flows on all pd from paths
        switches_in_paths = set().union(*paths)
        dp_written = []
        for node in switches_in_paths:
            dp_conf = self.net_config.dps[node]
            dp = dp_conf.dp_obj
            # dp_id = dp_conf.id

            match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src=packet.ip_src, ipv4_dst=packet.ip_dst)
            # инструкции - чтобы передать пакет на контроллер 
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER)])]
            # и продолжить его обработку
            inst += self.tables.goto_next_of(self.log_table)
            # готовим сообщение для добавления потока
            # приоритет = по умолчанию
            # cookie_mask=0 тк используется только при modify\delete (судя по документации)
            logflow_msg = parser.OFPFlowMod(datapath=dp, cookie=cookie, cookie_mask=0, table_id=self.log_table_id, match=match_ip, instructions=inst, command = ofproto.OFPFC_ADD, idle_timeout = packet.lifetime, hard_timeout = 0)
            
            # готовим сообщение по удалению потока
            # тк если надо записать поток 2, в то время как поток 1 еще на устройстве - модифицируем поток 1 меняя его куки (куки от потока 2) (отличия в потоках будет только в другом лог флаге пакета)
            # cookie и cookie_mask не указываем, тк удаляем с любым куки
            del_msg = parser.OFPFlowMod(datapath=dp,  table_id=self.log_table_id, command=ofproto.OFPFC_DELETE, out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY, match = match_ip)

            #формируем итоговое сообщение: вначале надо удалить старый поток - потом добавить новый - нужно это, тк add команда не сработает, если на контроллере уже есть подходящий поток - а modify команда не меняет cookie поля и не добавляет поток, если его нету
            # перед установкой потока, шлем поток, удаляющий старый поток
            tmp_msgs = [del_msg, dp.ofproto_parser.OFPBarrierRequest(datapath=dp), logflow_msg]
            util.send_msgs(dp, tmp_msgs)        

            # сохраняем лист datapath свитчей, чтобы потом можно было со всех них удалить при необходимости
            # TODO пока сохраняем список dp в ОЗУ, но лучше сохранять его хотя бы в БД, а лучше в кэш-сервере (redis, например). Чтобы пр иперезапуске контроллера данные не терялись
            # TODO при удалении dp из сети, он в !!! останется - надо ловить событие удаления dp из сети, и оттуда его тоже удалять
            dp_written.append(dp)
        self.dps_with_logflows[packet.ip_dst][packet.ip_src]=dp_written



    def install_paths(self, dpid_src, first_port, dpid_dst, last_port, mac_dst, ip_dst, src_vlan_id, paths, brd_dp = None, dst_vlan_id = None, vl_brd_dps = None, mac_src = None):
        computation_start = time.time() #just for info
        if dst_vlan_id is None:
            dst_vlan_id = src_vlan_id

        if paths is None:
            if src_vlan_id == dst_vlan_id:
                paths = self.get_optimal_paths(dpid_src, dpid_dst, src_vlan_id)
                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst)
            else:
                paths, vl_brd_dps = self.get_inter_vl_optimal_paths(dpid_src, dpid_dst, src_vlan_id, dst_vlan_id, last_port)
                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = 4096+src_vlan_id) #metadat тут = vlan_id, тк одновременно и ip и vlan мэтчить нельзя. Вообще можно TODO - возможно стоит изменить тогда тут на vlan id
            if len(paths) == 0:
                #если путей нет, то инсталим потоки, которые будут такие пакеты отбрасывать, время жизни потока - короткое
                dp_conf = self.net_config.dps[dpid_src]
                dp = dp_conf.dp_obj
                self.send_message (dp, self.ip_dstT_id, PRIORITY_DEF, match_ip, idle_timeout=60)
                return  

        pw = []
        for path in paths:
            pw.append(self.get_path_cost(path))
            # print (path, " Install paths:  Cost = ", pw[len(pw) - 1])
        sum_of_pw = sum(pw) * 1.0
        paths_with_ports = self.add_ports_to_paths(paths, first_port, last_port)
        switches_in_paths = set().union(*paths)

        if vl_brd_dps is None:
            vl_brd_dps = []

        for node in switches_in_paths:
            dp_conf = self.net_config.dps[node]
            dp = dp_conf.dp_obj

            vl_brd = False
            bef_brd = False
            aft_brd = False
            # определяем, является ли этот узел brd_vl_dp
            if dp_conf.id in vl_brd_dps:
                vl_brd = True

            # если узел не бордер, определяем  стоит ли он перед или после бордера
            if not vl_brd:
                for pth in paths:
                    for brd in vl_brd_dps:
                        if brd in pth:
                            # для каждого граничного узла ищем пути, в которых они присутсвуют
                            if node in pth:
                                # если узел, находится в таком листе
                                ni = pth.index(node)
                                bi = pth.index(brd)
                                if bi > ni:
                                    bef_brd = True
                                else:
                                    aft_brd = True

            if src_vlan_id == dst_vlan_id:
                # если маршрутизируем в пределах одного влана - всегда принимаем на вход потоки этого влана
                bef_brd = True

            if not vl_brd:
                if bef_brd: 
                    match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = src_vlan_id*1000)
                elif aft_brd:
                    match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000) #metadata тут = vlan_id, тк одновременно и ip и vlan мэтчить нельзя
            else:
                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = src_vlan_id*1000)
            
            # print('Install for ', node, ip_dst, vl_brd)
            if dp_conf.id == dpid_dst:
                # инсталлим потоки последнего gw
                if brd_dp is None:
                    # если не указан роутер, где объявлен шлюз по умолчанию, ищем его
                    brd_dp, *tmp = self.get_bord_swid(ip_dst)
                if dpid_dst != brd_dp:
                    self.install_last_dp_flows(node, brd_dp, mac_dst, ip_dst, vl_brd, src_vlan_id, dst_vlan_id, last_port)
                else:
                    self.install_border_dp_flows(brd_dp, mac_dst, ip_dst, vl_brd, src_vlan_id, dst_vlan_id, last_port)
            else:
                out_ports = {}
                i = 0
                for path in paths_with_ports:
                    if node in path:
                        out_port = path[node][1]
                        out_ports[out_port] = pw[i]
                    i += 1

                out_ports_len = len(out_ports)

                # нужно, чтобы различать потоки из одной и из разных сеток. Без этого при пинге хостов в одной сети, пути построятся, но припинге хостов из разных сетей - нет, тк путь до послденго роутера будет построен, но на нем линк будет транизтным learn host станет равным noneи пути на последнем роутере не построятся
                priority = PRIORITY_DEF
                if dp_conf.id == dpid_src:
                    # в ife только меняем match ip
                    if not self.is_gateway_mac(mac_dst):
                        # если ip_src в той же сетке что и ip_dst
                        # ищем какой сетке принадлежат
                        net = self.get_net_of_ip(brd_dp, ip_dst)
                        if net is None:
                            return
                        if not vl_brd:
                            if bef_brd: 
                                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = net, ipv4_dst=ip_dst, metadata = src_vlan_id*1000)
                            elif aft_brd:
                                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = net, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000)
                        else:
                            match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = net, ipv4_dst=ip_dst, metadata = src_vlan_id*1000, vlan_vid=(0x1000, 0x1000))
                            priority += 1
                        priority += 10
                    else:
                        #если в разных сетях
                        # если пакет пришел от внешних сетей
                        if first_port == dp_conf.ospf_out and dp_conf.ospf_out is not None:
                            print('Work this flow for ospf !!!!!! ', src_vlan_id, dst_vlan_id)
                            # ставим потоки для бордер свитча
                            match_ip = parser.OFPMatch(in_port = dp_conf.ospf_out, eth_type=0x0800, ipv4_dst=ip_dst, vlan_vid = 4096+src_vlan_id) #здесь не было этого: vlan_vid= 
                            priority += 50
                        else:
                            if not vl_brd:
                                if bef_brd: 
                                    match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = src_vlan_id*1000) 
                                elif aft_brd:
                                    match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000) 
                            else:
                                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = src_vlan_id*1000, vlan_vid=(0x1000, 0x1000))  #metadata тут = vlan_id, тк одновременно и ip и vlan мэтчить нельзя
                                priority += 1

                if out_ports_len > 1:
                    pw_list = []
                    buckets = []
                    for port, weight in out_ports.items():
                        bucket_weight = int(round((1 - weight/sum_of_pw) * 10))
                        # bucket_action = [parser.OFPActionOutput(port)]
                        if not vl_brd:
                            bucket_action = [parser.OFPActionSetField(metadata = port)]
                        else:
                            # print(' out_ports_len > 1 I work ', dp.id, dst_vlan_id, '  match = ', match_ip)
                            # bucket_action = [parser.OFPActionPopVlan()] #не работает, видимо тк идет сравнение по eth_type=0x0800, нет. Просто надо в сравнении обязательно vlan_vid указывать
                            bucket_action = [parser.OFPActionSetField(metadata = dst_vlan_id*1000 + port)]
                            self.install_metadata_change_flow(dp, dst_vlan_id, port)

                        nt = self.tables.next_table_id(self.eth_dst_table.name)
                        # bucket_action += [parser.OFPActionOutput(port)]
                        bucket_action += [parser.NXActionResubmitTable(table_id=nt, in_port = first_port)] #аналог go to table но в виде action
                        buckets.append(
                            parser.OFPBucket(
                                weight=bucket_weight,
                                watch_port=port,
                                watch_group=ofproto.OFPG_ANY,
                                actions=bucket_action
                            )
                        )
                        pw_list.append([port, weight])

                    gr_id, pw_num = self.get_groupid(node, pw_list, dst_vlan_id, vl_brd)

                    if gr_id is None:
                        # создать новую группу
                        group_id = self.generate_openflow_gid(node)
                        dp.send_msg(parser.OFPGroupMod(dp, ofproto.OFPGC_ADD, ofproto.OFPGT_SELECT, group_id, buckets))
                        dp.send_msg(dp.ofproto_parser.OFPBarrierRequest(dp))
                        # добавить к остальным группам
                        new_pw_num = self.generate_pw_list_key()
                        self.group_id[node][group_id][ip_dst] = new_pw_num
                        self.pw_lists[(new_pw_num, dst_vlan_id, vl_brd)] = pw_list
                        # print('New group = ', (new_pw_num, dst_vlan_id, vl_brd), pw_list)
                    else:
                        # print('Use the same group ', gr_id)
                        # нашли группу с такими же портами и весами, используем ее при создании потока
                        group_id = gr_id
                        # группа будет старой, добавить в нее новый ip
                        self.group_id[node][gr_id][ip_dst] = pw_num

                    gr_act = [parser.OFPActionGroup(group_id)]
                    # gr_act = [parser.OFPActionGroup(1)]

                    # перед установкой потока, шлем поток, удаляющий старый поток - нужно, тк не факт, что свитч overlapse поток будет заменять на новый, а не оставит старый
                    tmp_msgs = [ self.del_flow(dp, self.ip_dstT_id, match_ip, priority = priority, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME) ]
                    tmp_msgs += util.barrier_request(dp)
                    util.send_msgs(dp, tmp_msgs)
                    
                    self.send_message(dp, self.ip_dstT_id, priority, match_ip, actions = gr_act, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME)
                    
                    # print('@@@#########Group ids')
                    # for d in self.group_id.keys():
                    #     for g in self.group_id[d].keys():
                    #         print (d, g)
                    #         print(self.group_id[d][g].items())
                    # print('\n*****************self.pw_lists ')
                    # print(self.pw_lists.items())
                    # print('@@@@########## End')

                elif out_ports_len == 1:
                    pnum = list(out_ports.keys())[0]
                    # перед установкой потока, шлем поток, удаляющий старый поток - нужно, тк не факт, что свитч overlapse поток будет заменять на новый, а не оставит старый
                    tmp_msgs = [ self.del_flow(dp, self.ip_dstT_id, match_ip, priority = priority, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME) ]
                    tmp_msgs += util.barrier_request(dp)
                    util.send_msgs(dp, tmp_msgs)

                    if not vl_brd:
                        act = [parser.OFPActionSetField(metadata = pnum)]
                        inst = self.tables.goto_next_of(self.eth_dst_table)
                        self.send_message (dp, self.ip_dstT_id, priority, match_ip, inst, act, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME)
                    else:
                        # ставим потоки на влан бордер свитч
                        actions = [parser.OFPActionSetField(metadata = dst_vlan_id*1000 + pnum)]
                        # actions += [parser.OFPActionPopVlan()] #TODO когда в послдений раз работало - стояло это
                        inst = self.tables.goto_next_of(self.eth_dst_table)
                        self.send_message(dp, self.ip_dstT_id, priority, match_ip, inst, actions, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME)
                        self.install_metadata_change_flow(dp, dst_vlan_id, pnum)
                        
                    # запоминать такие порты
                    if ip_dst not in self.pnum_to_ip[node][pnum]:
                        self.pnum_to_ip[node][pnum].append(ip_dst)

        # инсталим потоки для swicthapp - передаем для каждого свитча сообщение о том, чтобы он записал src с определенного порта
        # if mac_src is NOne
        for path in paths_with_ports:
            for dpid, ports in path.items():
                dp = self.net_config.dps[dpid].dp_obj
                in_port_src = list(ports)[0]
                in_port_dst = list(ports)[1]
                event = c_ev.NewPredFlows_part2(dp_obj=dp, port_num=in_port_src, mac_src=mac_src)
                self.send_event_to_observers(event)
                event = c_ev.NewPredFlows_part2(dp_obj=dp, port_num=in_port_dst, mac_src=mac_dst)
                self.send_event_to_observers(event)

        print (f"Path installation on dp_src={dpid_src}, dp_dst={dpid_dst} for {ip_dst} finished in ", time.time() - computation_start)
        return True


    def install_last_dp_flows(self, dpid_src, brd_dp, mac_dst, ip_dst, vl_brd, src_vlan_id, dst_vlan_id, out_port):
        #ставим особые потоки для последнего свитча
        if not self.is_gateway_mac(mac_dst):
            # если ip_src в той же сетке что и ip_dst
            dp = self.net_config.dps[dpid_src].dp_obj
            # ищем какой сетке принадлежат
            net = self.get_net_of_ip(brd_dp, ip_dst)
            if net is None:
                print('Net is None')
                return 
            
            priority = PRIORITY_DEF + 100
            actions = []
            if not vl_brd:
                # если это не бордер свитч, значит принимать он будет влан назначения
                # TODO не изменил для теста
                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = net, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000)
                inst = self.tables.goto_next_of(self.ip_dst_table)
                # match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = net, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000+out_port)
                # inst = self.tables.goto_next_of(self.eth_dst_table)
            else:
                match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, ipv4_src = net, metadata = src_vlan_id*1000) #, vlan_vid=(0x1000, 0x1000)
                inst = self.tables.goto_next_of(self.eth_dst_table)
                inst += [parser.OFPInstructionWriteMetadata(metadata = dst_vlan_id*1000 + out_port, metadata_mask = 0xFFFFFFFF)]
                priority+=1
                # actions = [parser.OFPActionPopVlan()]
                print('!!install_last_dp_flows for the same vlan')
                self.install_metadata_change_flow(dp, dst_vlan_id, out_port)

            # перед установкой потока, шлем поток, удаляющий старый поток - нужно, тк не факт, что свитч overlapse поток будет заменять на новый, а не оставит старый
            tmp_msgs = [ self.del_flow(dp, self.ip_dstT_id, match_ip, priority = priority, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME) ]
            tmp_msgs += util.barrier_request(dp)
            util.send_msgs(dp, tmp_msgs)

            self.send_message (dp, self.ip_dstT_id, priority, match_ip, inst, actions, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME)
            return 

        #если в разных сетях - притворяемя шлюзом по умолчанию
        gw_mac, *tmp = self.get_gw_mac_ip_for_ip(brd_dp, ip_dst)
        if gw_mac is None:
            print('gw_mac is None   ', brd_dp, ip_dst)
            return 

        actions = []
        dp = self.net_config.dps[dpid_src].dp_obj
        priority = PRIORITY_DEF
        if not vl_brd:
            # TODO не изменил для теста
            match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000)
            inst = self.tables.goto_next_of(self.ip_dst_table)
            # match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = dst_vlan_id*1000+out_port)
            # inst = self.tables.goto_next_of(self.eth_dst_table)
        else:
            match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst, metadata = src_vlan_id*1000 )  #, vlan_vid=(0x1000, 0x1000)
            inst = self.tables.goto_next_of(self.eth_dst_table)
            inst += [parser.OFPInstructionWriteMetadata(metadata = dst_vlan_id*1000 + out_port, metadata_mask = 0xFFFFFFFF)]
            # actions = [parser.OFPActionPopVlan()]
            priority+=1
            print('!!install_last_dp_flows for the different vlan')
            self.install_metadata_change_flow(dp, dst_vlan_id, out_port)

        mac_dst = self.get_mac_of_ip(ip_dst)
        actions += [parser.OFPActionSetField(eth_dst = mac_dst)] 
        actions += [parser.OFPActionSetField(eth_src = gw_mac)]

        # перед установкой потока, шлем поток, удаляющий старый поток - нужно, тк не факт, что свитч overlapse поток будет заменять на новый, а не оставит старый
        tmp_msgs = [ self.del_flow(dp, self.ip_dstT_id, match_ip, priority = priority, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME) ]
        tmp_msgs += util.barrier_request(dp)
        util.send_msgs(dp, tmp_msgs)

        self.send_message (dp, self.ip_dstT_id, priority, match_ip, inst, actions, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME)


    def install_border_dp_flows(self, brd_dp, mac_dst, ip_dst, vl_brd, src_vlan_id, dst_vlan_id, out_port):
        dp_of_ip, *tmp = self.find_ipdp(ip_dst)
        if brd_dp == dp_of_ip:
            #обработка ситуации, когда роутер бордер и к нему присоединен искомый хост
            return self.install_last_dp_flows(brd_dp, brd_dp, mac_dst, ip_dst, vl_brd, src_vlan_id, dst_vlan_id, out_port)
        print('Its host border dp')
        # если шлем во внешнюю сеть, то ничего не делать. ospfapp сам заинсталит нужные потоки по пересылке траффика. Но сам метод нужен, именно, чтобы ничего не делать
        

    def get_paths(self, src, dst, vid):
        ''' Get all paths from src to dst using DFS algorithm 
        src - dp id of source switch
        dst - dp id of dst switch'''
        # print('get_paths  ', src, ' ', dst, ' ', vid)
        # print(self.adjacency.items())
        if src == dst:
            # host target is on the same switch
            return [[src]]
        paths = []
        stack = [(src, [src])]
        while stack:
            (node, path) = stack.pop()
            for next in set(self.adjacency[node].keys()) - set(path):
                #чтобы искать только пути для определнного влана используем check_pvlan_eq_vl
                pnum = self.adjacency[node][next]
                if next is dst and self.check_pvlan_eq_vl(vid, node, pnum):
                    paths.append(path + [next])
                else:
                    stack.append((next, path + [next]))
        # print ("Available paths from ", src, " to ", dst, " : ", paths)
        return paths


    def get_inter_vl_paths(self, src, dst, src_vid, dst_vid):
        ''' Get all paths from src to dst using DFS algorithm 
        src - dp id of source switch
        dst - dp id of dst switch'''
        if src == dst:
            # host target is on the same switch
            return [[src]]
        paths = []
        stack = [(src, [src])]
        while stack:
            (node, path) = stack.pop()
            for next in set(self.adjacency[node].keys()) - set(path):
                #чтобы искать только пути для определнного влана используем check_pvlan_eq_vl
                pnum = self.adjacency[node][next]
                if next is dst and self.check_pvlan_eq_vl(src_vid, node, pnum, dst_vid):
                    paths.append(path + [next])
                else:
                    stack.append((next, path + [next]))
        return paths
    

    def get_link_cost(self, s1, s2):
        ''' Get the link cost between two switches  '''
        p1 = self.adjacency[s1][s2]
        p2 = self.adjacency[s2][s1]
        try:
            p1_conf = self.net_config.dps[s1].ports[p1]
            p2_conf = self.net_config.dps[s2].ports[p2]
            #второй вариант, если не те вланы, то делать вес очень большим, но надо тогда еще влан передавать
            pw = REFERENCE_BW/min(p1_conf.speed, p2_conf.speed)
        except KeyError as er:
            print(f"Got exeption {er} \nMaybe there are links in the net, that are not mentioned in configuration?")
            raise KeyError (er)
        return pw

    def get_path_cost(self, path):
        ''' Get the path cost '''
        cost = 0
        for i in range(len(path) - 1):
            cost += self.get_link_cost(path[i], path[i+1])
        return cost

    def get_optimal_paths(self, src, dst, vid):
        ''' Get the n-most optimal paths according to MAX_PATHS '''
        paths = self.get_paths(src, dst, vid)
        paths_count = len(paths) if len(paths) < MAX_PATHS else MAX_PATHS
        return sorted(paths, key=lambda x: self.get_path_cost(x))[0:(paths_count)]

    def get_inter_vl_optimal_paths(self, src, dst, src_vid, dst_vid, last_port):
        ''' Get the n-most optimal paths according to MAX_PATHS '''
        paths = self.get_inter_vl_paths(src, dst, src_vid, dst_vid)
        paths_count = len(paths) if len(paths) < MAX_PATHS else MAX_PATHS

        srt_paths = sorted(paths, key=lambda x: self.get_path_cost(x))      
        brd_dps = []
        res_paths = []
        i = 0
        # проверяем, можно ли дойти из влана в другой влан через путь, так чтобы был brd_dp - то есть, чтобы можно было в какой-то момент времени сменить src_vl на dst_vl
        for path in srt_paths:
            brd_vl_dp = self.get_vl_brd_dp(path, src_vid, dst_vid, last_port)
            if brd_vl_dp is not None:
                res_paths.append(path)
                brd_dps.append(brd_vl_dp)
                i+= 1
                if i >= paths_count:
                    break

        return res_paths, set(brd_dps)


    def add_ports_to_paths(self, paths, first_port, last_port):
        ''' Add the ports that connects the switches for all paths '''
        paths_p = []
        # print(f"!!!add_ports_to_paths paths={paths}\n first_port={first_port}, last_port={last_port}")
        # print(f"\n!!!self.adjacency = {self.adjacency}")
        for path in paths:
            p = {}
            in_port = first_port
            for s1, s2 in zip(path[:-1], path[1:]):
                out_port = self.adjacency[s1][s2]
                p[s1] = (in_port, out_port)
                in_port = self.adjacency[s2][s1]
            p[path[-1]] = (in_port, last_port)
            paths_p.append(p)
        # print(f"\n!!!result = {paths_p}")
        return paths_p
    
    def check_pvlan_eq_vl(self, vid, dpid, pnum, vid_dst = None):
        # проверяем есть ли в указанном порту нужный vid
        try:    
            pconf = self.net_config.dps[dpid].ports[pnum]
        except KeyError as e:
            print('Error ', e, dpid, pnum)
        if pconf.tagged_vlans is not None:
            for vl in pconf.tagged_vlans:
                vlan_num = self.net_config.vlans[vl].vid
                if vid == vlan_num or (vid_dst is not None and vid_dst == vlan_num):
                    return True
        else:
            vlan_num = self.net_config.vlans[pconf.native_vlan].vid
            if vid == vlan_num or (vid_dst is not None and vid_dst == vlan_num):
                return True
        return False


    def get_vl_brd_dp(self, path, src_vl_id, dst_vl_id, dst_port_num):
        # ищем граничный свитч для двух вланов. Такой свитч - ближайший к точке назначения, и на самом свитче влан назначения приходит во входной порт, но в выходном порту уже используется влан источника
        # TODO пока что возвращается первый свитч у которого нету dp_vl, в будущем можно возвращать список свитчей, на которых требуется менять влан
        vl_dst_name = None
        for vl in self.net_config.vlans.values():
            if vl_dst_name is None and vl.vid == dst_vl_id:
                vl_dst_name = vl.name
                break
        if vl_dst_name is None:
            print('Error: vl_dst_name is None')
            return None

        paths_num = len(path)
        i = paths_num - 1
        while i >= 0:
            dp0 = None
            dp1 = None
            dp2 = None
            # берем по два свитча соседа
            if i != paths_num - 1:
                # если не рассматриваем первый свитч с конца
                dp2 = path[i+1]
            dp1 = path[i]
            if i - 1 >= 0:
                dp0 = path[i-1]
            else:
                dp0 = None

            if dp0 is None:
                # дошли до связки src_dp-host, значит от src_dp до конечного dp протянут vl_dst, значит менять влан можно на src_dp
                return dp1
            
            p_in_num = self.adjacency[dp1][dp0]
            p_in = self.net_config.dps[dp1].ports[p_in_num]

            if dp2 is None:
                # если работаем с линком dst_dp-host
                p_out = p_out = self.net_config.dps[dp1].ports[dst_port_num] 
            else:
                p_out_num = self.adjacency[dp1][dp2]
                p_out = self.net_config.dps[dp1].ports[p_out_num]
            
            dst_vl_is_out = self._vl_in_port(vl_dst_name, p_out)
            dst_vl_is_in = self._vl_in_port(vl_dst_name, p_in)
            if not dst_vl_is_in and dst_vl_is_out:
                # если влан присутсвует в порту dp1 (на выход), но остутсвует в порту dp2 (на вход), значит надо менять влан метку в dp1
                return dp1
            elif dst_vl_is_in and not dst_vl_is_out:
                # может быть вариант dst_vl_is_in но not dst_vl_is_out, тк при расчете пути мы просто смотрим на то, есть ли один из двух вланов на портах, поэтому в  get_inter_vl_optimal_paths - ловим None из этого метода
                return None
            
            i-=1


    def _vl_in_port(self, vl_name, port):
        if port.tagged_vlans is not None:
            if vl_name in port.tagged_vlans:
                return True
        elif port.native_vlan == vl_name:
            return True
        return False

    def install_metadata_change_flow(self, dp, dst_vlan_id, pnum):
        # делаем обработку метадаты
        match = parser.OFPMatch(metadata = dst_vlan_id*1000 + pnum, vlan_vid=(0x1000, 0x1000) )
        # actions = [parser.OFPActionPopVlan()]
        # actions += [parser.OFPActionPushVlan()]
        actions = [parser.OFPActionSetField(vlan_vid = 4096+dst_vlan_id)]
        inst = [parser.OFPInstructionWriteMetadata(metadata = pnum, metadata_mask = 0xFFFFFFFF)]
        inst += self.tables.goto_next_of(self.vl_change_table)
        self.send_message(dp, self.vl_changeT_id, PRIORITY_DEF, match, inst, actions, idle_timeout=IDLE_TIME, hard_timeout=HARD_TIME)


#ARP cache methods
    def learn_host(self, dpid, ip_adr, port, mac):
        # print('Start to learn host ', dpid, ip_adr, port, mac)
        if self.port_is_link(dpid, port):
            return None

        msgs = []
        #check if the ip is already in some port, if on port - delete, add to new port
        self.check_arp_time()
        # ip_adr = ip_interface(ip_adr)
        record = self.arp_cache.get(ip_adr)
        if record is not None:
            if record.port == port and record.dpid == dpid:
                #обновляем арп запись, если порт не изменился
                #TODO а если мас другой пришел?
                record.timestamp = time.time()
                return msgs
            else:
                #удаляем старую запись и потоки, связанные с этим портом
                del self.arp_cache[ip_adr]
                dp = self.net_config.dps[dpid].dp_obj
                msgs = [self.del_flow(dp, self.ip_dstT_id, match = parser.OFPMatch(ipv4_dst=ip_adr))]
                msgs += util.barrier_request(dp)
        #проверяем по adj что порт не линковочный 

        #добавляем запись
        self.arp_cache[ip_adr] = ArpRecord(dpid, mac, port)
    
        # print('#####Learn host ', self.port_is_link(dpid, port), ip_adr)
        # print('Arp cahce items = ', self.arp_cache.items())
        # print('!!OTHER STUFF')
        # try:
        #     print('arp_cache[ip_adr]  =  ', self.arp_cache[ip_adr])
        # except KeyError as e:
        #     print('@@@Exc got')
        #     print(e)
        # print('Adjendency')
        # for c in self.adjacency.keys():
        #     for a, b in self.adjacency[c].items():
        #         print(c, a, b)
        # print('End of learn\n')
        return msgs

    def check_arp_time(self):
        "Clean entries older than timeout"
        curtime = time.time()
        for ip, record in self.arp_cache.items():
            if record.timestamp + DEF_ARP_DEAD_TIME < curtime:
                del self.arp_cache[ip]

    def check_queue_time(self):
        "Clean entries older than timeout"
        curtime = time.time()
        # new_queue = defaultdict(dict)
        for dst, srcs in self.pkt_queue.items():
            for src_lists in srcs.values():
                src_list_index = 0
                for s_list in src_lists:
                    #проверяем время каждого листа [packet, time]
                    clock = s_list[1]
                    # print('CHASI = ', clock, type(clock))
                    if clock + DEF_QUEUE_DEAD_TIME < curtime:
                        # new_queue[dst] = srcs
                        src_lists.pop(src_list_index) #TODO need test, if pop is correct
                    else:
                        #тк элементы при удалении сдвигаются, то увеличивать счетчик надо только, если элем не удален
                        src_list_index += 1


    def find_ipdp(self, ip_adr):
        #ip_adr is string= x.x.x.x
        record = self.arp_cache.get(ip_adr)
        #find gateway_for_ip
        gateway_for_ip = None
        br_flag = False
        for dp_conf in self.net_config.dps.values():
            if dp_conf.ospf_out is not None:
                for ip_int in dp_conf.announced_gws.values():
                    ip_adr = ip_address(ip_adr)
                    if ip_adr in ip_int.network:
                        gateway_for_ip = ip_int.ip
                        br_flag = True
                        break
            if br_flag:
                break

        if record is None:
            return None, None, gateway_for_ip 
        else:
            return record.dpid, record.port, gateway_for_ip
        
    def port_is_link(self, dpid, pnum):
        dp2_dict = self.adjacency.get(dpid)
        if dp2_dict is None:
            #возвращаем значение, тк хост может быть присоединен непосредственно только к одному свитчу
            #TODO а как со стеком быть или дугой такой ситуацией, когда хосты присоединяются к нескольким свитчам сразу?
            return False
        dp2 = util.get_key(dp2_dict, pnum)
        if dp2 is None:
            #считаем, что хост присоединен только к одной DP, поэтому если связи с другим свитчом на порту нет, а хост есть, значит хост присоединен непосредственно к этому свитчу
            #для обработки линка ospf свитча с vedge. Нужно тк такой линк в adj не отражается. Проверяем, не является ли найденный свитч граничным, и если да, то не совпадает ли порт с линк оспф портом
            dp1 = self.net_config.dps[dpid]
            if dp1.ospf_out is not None:
                if dp1.ospf_out != pnum:
                    return False
            else:
                return False
        return True


    def is_gateway(self, ip):
        #проверяет, является ли адрес, адресом шлюза по умолчанию
        for dp in self.net_config.dps.values():
            if dp.ospf_out is not None:
                for gw in dp.announced_gws.values():
                    if str(ip) == str(gw.ip):
                        return True
                for gw in dp.other_gws.values():
                    if str(ip) == str(gw.ip):
                        return True
        return False

    def is_gateway_mac(self, mac):
        #проверяет, является ли mac адрес, mac адресом шлюза по умолчанию
        for dp in self.net_config.dps.values():
            if dp.ospf_out is not None:
                for gw_mac in dp.announced_gws.keys():
                    if str(mac) == str(gw_mac):
                        return True
                for gw in dp.other_gws.keys():
                    if str(gw_mac) == str(gw_mac):
                        return True
        return False

#----
    
    def get_bord_swid(self, ip):
        switches = []
        ip_adr = ip_address(ip)
        for sw in self.net_config.dps.values():
            if sw.ospf_out is not None:
                for sw_gw in sw.announced_gws.values():
                    gw = ip_interface(sw_gw)
                    if ip_adr in gw.network:
                        switches += [[sw.id, sw_gw]]
                for sw_gw in sw.other_gws.values():
                    gw = ip_interface(sw_gw)
                    if ip_adr in gw.network:
                        switches += [[sw.id, sw_gw]]

        sw_ln = len(switches)
        if sw_ln == 1:
            return switches[0][0], switches[0][1]
        elif sw_ln == 0:
            return -1, -1
        else:
            return switches[0][0], switches[0][1]
            # return set(switches) #TODO (не работает корректно щас ypeError: unhashable type: 'list') Возвращаем set бордер свитчей, чтобы обрабатывать ситуации, когда в сети больше 1 бордер свитча
    
    def get_gw_mac_ip_for_ip(self, dpid, ip):
        # get gateway mac for this ip
        ip_adr = ip_address(ip)
        try:
            dp = self.net_config.dps[dpid]
        except KeyError as e:
            print('Error: L3 get_gw_mac_ip_for_ip = ', e)
            return None, None
            
        if dp.ospf_out is not None:
            for mac, sw_gw in dp.announced_gws.items():
                gw = ip_interface(sw_gw)
                if ip_adr in gw.network:
                    return mac, gw.ip
            for mac, sw_gw in dp.other_gws.items():
                gw = ip_interface(sw_gw)
                if ip_adr in gw.network:
                    return mac, gw.ip
        return None, None
    
    def get_net_of_ip(self, dpid, ip):
        ip_adr = ip_address(ip)
        dp = self.net_config.dps[dpid]
        if dp.ospf_out is not None:
            for mac, sw_gw in dp.announced_gws.items():
                gw = ip_interface(sw_gw)
                if ip_adr in gw.network:
                    return gw.network.with_netmask
            for mac, sw_gw in dp.other_gws.items():
                gw = ip_interface(sw_gw)
                if ip_adr in gw.network:
                    return gw.network.with_netmask
        return None

        
    def get_mac_of_ip(self, ip):
        # get mac of this ip in arp table of dp
        return self.arp_cache[ip].mac

    def get_mac_of_gw(self, ip):
        for dp in self.net_config.dps.values():
            if dp.ospf_out is not None:
                for mac, gw in dp.announced_gws.items():
                    if str(ip) == str(gw.ip):
                        return mac
                for mac, gw in dp.other_gws.items():
                    if str(ip) == str(gw.ip):
                        return mac
        return None


    def get_groupid(self, dpid, port_weight_list, vl_id, change_vl):
        # ищет group id для указанного dpid и листа вида [(port_num1, w1), (port_num2, w2)]
        # self.group_id = #{dpid:{gid:{ip: id из pw_lists } } }
        # print('!Get group id for ', dpid, port_weight_list, vl_id)
        # print("All groups = ", self.group_id)
        # print("Specific group = ", self.group_id[dpid])
        for gid in self.group_id[dpid].keys():
            #просматриваем листы каждого ip
            # print('group vlues = ', self.group_id[dpid][gid].values(), '\n', self.group_id[dpid][gid])
            for plist_num in self.group_id[dpid][gid].values():
                plist = self.pw_lists[(plist_num,vl_id, change_vl)]
                # print('---------\nPlist = ', plist, ' for ', plist_num,vl_id, change_vl)
                if set(map(tuple, plist)) == set(map(tuple,port_weight_list)):
                    return gid, plist_num
        return None, None

    def generate_openflow_gid(self, dpid):
        '''  Returns a random OpenFlow group id '''
        n = random.randint(1000, 2**32)
        # проверка того, есть ли указанный номер группы в сохраненных номерах групп
        all_grid = list(self.group_id[dpid].keys())
        while n in all_grid:
            n = random.randint(0, 2**32)
        return n

    def generate_pw_list_key(self):
        #генерируем новый уникальный номер для pw_lists
        all_keys = set(self.pw_lists.keys())
        keys_l = len(all_keys)
        # ищем пробелы в числах, например , если етсь 1 3 4, то вернем 2
        for i in range(keys_l):
            if i not in all_keys:
                return i
        return keys_l+1

    def clean_all_flows(self, dp):
        "Remove all flows with the App cookie from all tables"
        msgs = []
        for t in self.tables.tables:
            i = self.tables.table_id(t.name)
            msgs += [self.del_flow (dp, i)]
        # удалить ВСЕ select группы, тк модуль использует только их
        msgs+=[parser.OFPGroupMod(dp, ofproto.OFPGC_DELETE, ofproto.OFPGT_SELECT, group_id = ofproto.OFPG_ALL)]
        return msgs

    def clean_ip_flows(self, dpid, pnum):
        # удаляет все потоки и группы, связанные с опрделенным портом
        # self.group_id = defaultdict(lambda:defaultdict(dict) ) # self.group_id = #{dpid:{gid:{ip: id из pw_lists } } }
        # self.pw_lists = defaultdict(list) # {id: [ [port_num1, w1], [port_num2, w2] ] }

        print(f'clean_ip_flows for dpid={dpid}, pnum = {pnum}')
        # обработка ситуации, когда порт не в группе  (те вывод пакета идет только через этот порт)
        # сохраняем список айпи, которые должны быть удалены
        norm_ips = self.pnum_to_ip[dpid][pnum]

        # обработка ситуации, когда порт в группе
        groups = {}
        for gid, iplist in self.group_id[dpid].items():
            ips = []
            for ip, pwid in iplist.items():
                # смотрим все gid и ip и pwid в них
                for pset in self.pw_lists[pwid]:
                    # проверяем, есть ли порт в pwid группе
                    if pnum == pset[0]:
                        ips.append(ip)
                        # если нашли порт то дальше pwid группу просматривать нет смысла
                        break
            groups[gid] = ips

        # получили список gid с ips, которые связаны с этим портом
        groups_ids = groups.keys()
        # удаляем эти группы из dp
        ips = groups.values()
        dp = self.net_config.dps[dpid].dp_obj
        msgs = []
        for gid in groups_ids:
            msgs+=[parser.OFPGroupMod(dp, ofproto.OFPGC_DELETE, ofproto.OFPGT_SELECT, group_id = gid)]
        util.send_msgs(dp, msgs)
        # удаляем потоки с этими ip со всех dp
        for dp in self.net_config.dps.values():
            if dp not in self.net_config.active_dps.values():
                continue
            dp = dp.dp_obj
            msgs = []
            for iplist in ips:
                for ip in iplist:
                    msgs += [self.del_flow(dp, self.ip_dstT_id, match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip))]
            for ip in norm_ips:
                msgs += [self.del_flow(dp, self.ip_dstT_id, match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip))]
            msgs += util.barrier_request(dp)
            util.send_msgs(dp, msgs)

        
        # print('@@@#########BEFORE CLEAN Group ids')
        # for d in self.group_id.keys():
        #     for g in self.group_id[d].keys():
        #         print (d, g)
        #         print(self.group_id[d][g].items())
        # print('\n*****************self.pw_lists ')
        # print(self.pw_lists.items())
        # print('@@@@########## End')

        # чистим словари
        # print('Delete dictionary = ', dpid, pnum, self.pnum_to_ip[dpid][pnum])
        del self.pnum_to_ip[dpid][pnum]
        for gid, ips in groups.items():
            gdict = self.group_id[dpid][gid]
            for ip in ips:
                del gdict[ip]
            if len(gdict.values()) < 0:
                del gdict
        
        # print('Deleteddd dictionary = ', self.pnum_to_ip[dpid].get(pnum))
        # print dictionary delete
        # print('@@@#########Group ids')
        # for d in self.group_id.keys():
        #     for g in self.group_id[d].keys():
        #         print (d, g)
        #         print(self.group_id[d][g].items())
        # print('\n*****************self.pw_lists ')
        # print(self.pw_lists.items())
        # print('@@@@########## End')
    

    def _get_vlan(self, ip_dst):
        # print(' _get_vlan FOR ip_dst = ', ip_dst)
        for vl in self.net_config.vlans.values():
            # print('VL in get_vlan = ', vl)
            if vl.net is not None:
                for net in vl.net:
                    net = ip_network(net)
                    ip_adr = ip_address(ip_dst)
                    if  ip_adr in net:
                        # если айпи назначения принадлежит сетке, которая принадлежит этому влану - вернуть номер влана
                        return vl.vid
        return None


    def make_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        return util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)

    def send_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        # используется если сообщение ловится одним dp, а слать нужно на другой dp
        m = util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)
        # print('!!Send message')
        # print(datapath.id, '\n', match, table_id, '\n', m)
        datapath.send_msg(m)

    def del_flow(self, dp, table_id = None, match = None, out_port=None, out_group=None, priority = 32768, idle_timeout = 0, hard_timeout = 0):
        return util.del_flow(dp, self.cookie, table_id, match, out_port, out_group, priority = priority, idle_timeout = idle_timeout, hard_timeout = hard_timeout)



class ArpRecord:
    def __init__(self, dpid, mac, port_num):
        self.dpid = dpid
        self.mac = mac
        self.port = port_num
        self.timestamp = time.time()

    def __str__(self):
        args = []
        args.append('<ArpRecord')
        for prop in util.props(self):
            args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ''.join(args)