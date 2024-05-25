# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.ofproto import ofproto_v1_3 as ofproto
from ryu.ofproto import ofproto_v1_3_parser as parser
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls

from ryu.lib.packet import packet, ethernet, ospf, arp, ipv4, vlan
from array import array
from collections import defaultdict
from ipaddress import ip_interface, ip_network, ip_address
import ospf_util
import helper_methods as util
import table
import time
import ofp_custom_events as c_ev
from config import Config


PRIORITY_MIN = 0
PRIORITY_DEF = 16000
PRIORITY_MAX = 32000
DEAD_INTERVAL = 40

class Ospf(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto.OFP_VERSION]

    _CONTEXTS = {
        'tables': table.Tables,
        'net-config': Config
        }

    _EVENTS = [c_ev.PortNeedClean]  # TODO delete if ospf app works отовсюду #c_ev.NeighMacChanged


    def __init__(self, *args, **kwargs):
        super(Ospf, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.net_config = kwargs['net-config']
        self.cookie = 40
        self.area = '0.0.0.0'
        self.route_id = '0.0.0.1'
        self.ip_dst_table, self.ip_dstT_id = self.tables.get_table('ip_dst')
        self.eth_dst_table, self.eth_dstT_id = self.tables.get_table('eth_dst')
        self.neighours_mac = defaultdict(dict) # { dpid: {ip_neughbor: mac} }
        self.def_flows_not_set = {} # { dp_id : True }
        self.rid_neighours = defaultdict(list)  # {dpid : [(ospf_router_id, time)] }
        self.drs = {}  # { dpid: designed_router }
        self.dbd_acks = {} # {dpid : бит подтверждения dbd}
        self.seq = {} # {dpid : seq_num} #eго надо увеличивать при изменении сеток
        self.lsas = defaultdict(list) # {dpid : [lsa] }


    @set_ev_cls(c_ev.NewDp, CONFIG_DISPATCHER)
    def _new_switch_handler(self, ev):
        dp = ev.dp
        #удаляем все прошлые правила
        msgs = self.clean_all_flows(dp)
        dp_conf = self.net_config.dps[dp.id]
        # если граничный свитч
        if dp_conf.ospf_out is not None:
            actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER)] #max len не указываем, тк пакет оспф может быть большим
            #добавляем правило на пересылку ip к контроллеру всего ospf траффика
            match = parser.OFPMatch(eth_type=0x0800, ip_proto = 89)
            msgs += [self.make_message (dp, 0, PRIORITY_MAX, match, actions = actions)]
        util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.NewAnnouncedGw, MAIN_DISPATCHER)
    def _change_gateways_handler(self, ev):
        # если известен mac соседа-роутера - нужно, чтобы выполнять действия после того, как установлена смежность с роутером-соседом 
        dp = ev.dp
        neigh_macs = self.neighours_mac.get(dp.id)
        dp_conf = self.net_config.dps[dp.id]
        neigh_num = len(self.rid_neighours[dp.id]) #ищется длина списка списков, поэтому непонятно TODO это будет длина всех элементов всех списков или число списков в списке rid_neighbors?
        if neigh_macs is None or neigh_num > 0 or dp_conf.ospf_out is None:
            return
        
        gw_mac = ev.mac
        gw_ip = ev.int_ip
        
        # считаем, что у нас всегда одни gw
        # если что, можно использовать это для работы со всеми маками хранящимися для свитча
            # for neigh_ip, neigh_mac in neigh_macs.items():
        neigh_mac = list(neigh_macs.values())[0]
        neigh_ip = list(neigh_macs.keys())[0]

        # инсталим поток нового gw
        gw_net = gw_ip.network.with_netmask
        msgs = self.set_def_gw_flow(dp, gw_net, gw_mac, neigh_mac)

        # определим, являемся ли мы designed routerом в ospf
        dr = self.drs.get(dp.id)
        if dr is None or dr == self.route_id:
            # значит designed router - мы
            neigh_mac = '01:00:5e:00:00:05'
            neigh_ip = '224.0.0.5'

        # объявляем новый lsa
        net = ospf.ASExternalLSA.ExternalNetwork(mask=str(gw_ip.network.netmask), metric=20, fwd_addr='0.0.0.0')
        lsa = ospf.ASExternalLSA( id_=str(gw_ip.ip), extnws=[net], adv_router=self.route_id, ls_seqnum=self.seq[dp.id], options = 0x20)
        
        # увеличим seq для dp
        self.seq[dp.id] += 1 
        self.save_lsa(dp.id, lsa)

        util.send_msgs(dp, msgs)
        self.ospf_upd(dp, ip_dst = neigh_ip, mac_dst = neigh_mac, lsas = [lsa])


    @set_ev_cls(c_ev.DelAnnouncedGw, MAIN_DISPATCHER)
    def _del_gateway_handler(self, ev):
        dp = ev.dp
        gw_ip = ev.int_ip
        gw_mac = ev.mac
        
        # TODO TEST
        if self.del_anGw_from_queue(dp, gw_ip, gw_mac):
            # если нашли и удалили элемент из очереди, значит событие и потоки применены не были, очищать их не надо
            return 

        # удаляем поток этого gw
        neigh_macs = self.neighours_mac.get(dp.id)
        neigh_mac = list(neigh_macs.values())[0]
        neigh_ip = list(neigh_macs.keys())[0]
        gw_net = gw_ip.network.with_netmask
        
        # удаляем lsa из словаря
        for lsa in self.lsas[dp.id]:
            # TODO тут используется статическая metric при определении нужного lsa
            net = ospf.ASExternalLSA.ExternalNetwork(mask=str(gw_ip.network.netmask), metric=20, fwd_addr='0.0.0.0')
            if lsa.id_ == str(gw_ip.ip) and lsa.extnws[0] == net:
                # нашли нужный lsa 
                # меняем метрику на 16777215 - вроде бы это будет считаться за удаление маршрута с точки зрения OSPF
                lsa.extnws[0].metric = 16777215

                # увеличим seq для dp TODO для каждой lsa надо бы свой seq задавать
                self.seq[dp.id] += 1 
                lsa.ls_seqnum = self.seq[dp.id]
                # шлем объявление об удалении lsa 
                util.send_msgs(dp, self.del_def_gw_flow(dp, gw_net, gw_mac, neigh_mac) )
                self.ospf_upd(dp, ip_dst = neigh_ip, mac_dst = neigh_mac, lsas = [lsa])
                # удаляем из словаря
                del lsa
                break


    @set_ev_cls(c_ev.NewGw, MAIN_DISPATCHER)
    def _new_other_gateway_handler(self, ev):
        # если известен mac соседа-роутера - нужно, чтобы выполнять действия после того, как установлена смежность с роутером-соседом 
        dp = ev.dp
        neigh_macs = self.neighours_mac.get(dp.id)
        dp_conf = self.net_config.dps[dp.id]
        neigh_num = len(self.rid_neighours[dp.id]) #ищется длина списка списков, поэтому непонятно TODO это будет длина всех элементов всех списков или число списков в списке rid_neighbors?
        if neigh_macs is None or neigh_num > 0 or dp_conf.ospf_out is None:
            return
        
        gw_mac = ev.mac
        gw_ip = ev.int_ip
        neigh_mac = list(neigh_macs.values())[0]
        # neigh_ip = list(neigh_macs.keys())[0]
        # инсталим поток нового gw
        gw_net = gw_ip.network.with_netmask
        util.send_msgs(dp, self.set_def_gw_flow(dp, gw_net, gw_mac, neigh_mac) )


    @set_ev_cls(c_ev.DelGw, MAIN_DISPATCHER)
    def _del_other_gateway_handler(self, ev):
        dp = ev.dp
        neigh_macs = self.neighours_mac.get(dp.id)
        gw_mac = ev.mac
        gw_ip = ev.int_ip
        # удаляем поток этого gw
        gw_net = gw_ip.network.with_netmask
        neigh_mac = list(neigh_macs.values())[0]
        util.send_msgs(dp, self.del_def_gw_flow(dp, gw_net, gw_mac, neigh_mac) )


    # @set_ev_cls(c_ev.NewBorderRouter, MAIN_DISPATCHER)
    # def _new_br_handler(self, ev):
    #     return


    @set_ev_cls(c_ev.DelBorderRouter, MAIN_DISPATCHER)
    def _del_br_handler(self, ev):
        dp_conf = ev.dp_conf
        dp = dp_conf.dp_obj
        self.clean_all_flows(dp)
        # вызвать событие чистки l3 таблицы для всех портов свитча
        events = []
        for port in dp_conf.ports.values():
            events += [c_ev.PortNeedClean(self, dp_conf.dp_obj, port)]
        for ev in events:
            self.send_event_to_observers(ev)


    def set_default_flows(self, dp, neigh_mac, neigh_ip):
        # устанваливаем потоки по пресылке траффику на шлюз по умолчанию
            # эти мэтчи должны работать предпоследними - перед отправкой неизвестных айпи на роутер. Нужны для отправки всех пакетов, которые пришли не из 5 порта дальше. Тк мэтч по не5 порту не может ставиться, то будем разрешать пересылку всех пакетов из сетей brd dp во внешние сети.
            # единственная проблема, с таким потоком не выйдет подключать пользовательские утсройства к border ospf
            # match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = из всех сеток на бордер свитче)
            # решение - ставить такие потоки
        dp_conf = self.net_config.dps[dp.id]
        gw_ip, bitmask, gw_mac = self.get_gw_info_for_ip(dp.id, neigh_ip)
        msgs = []
        if gw_mac is None:
            print('get_gw_info_for_ip = ', dp.id, neigh_ip, gw_ip, bitmask, gw_mac)
            return msgs

        # установить потоки по умолчанию для свитча с адресом роутера-соседа
        for ip_int in dp_conf.announced_gws.values():
            net = ip_int.network.with_netmask
            msgs += self.set_def_gw_flow(dp, net, gw_mac, neigh_mac)

        for ip_int in dp_conf.other_gws.values():
            net = ip_int.network.with_netmask
            msgs += self.set_def_gw_flow(dp, net, gw_mac, neigh_mac)
        return msgs


    def set_def_gw_flow(self, dp, gw_with_netmask, gw_mac, neigh_mac):
        # обрабатываем ситуацию, когда пользовательские устройства присоединены к свитчу и общаются друг с другом, но пока такого потока не построено
        # ipv4_dst = из всех сеток на этом бордер свитче
        match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst = gw_with_netmask)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER)] #max len тут тоже не добавляем, а то мало ли что
        msgs = [self.make_message (dp, self.ip_dstT_id, PRIORITY_MIN+5, match_ip, actions = actions)]
        # обрабатывать ситуации, когда хосты все же хотят общаться с внешними сетями, тогда меняять маки и слать дальше к роутеру
        # ipv4_src = из всех сеток на этом бордер свитче
        
        # пока нетTODO удалить тогда из других мест здесь работу с такими же потоками
        # нет - не подходит, поэтому инсталить будем по отдельным src и в l3app  
        match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = gw_with_netmask)
        # actions = [parser.OFPActionPopVlan()]
        actions = [parser.OFPActionSetField(eth_src = gw_mac)]
        actions += [parser.OFPActionSetField(eth_dst = neigh_mac)]
        # action += [parser.OFPActionSetField(metadata = БИТ для смены влана)]
        inst = self.tables.goto_next_of(self.ip_dst_table)
        # inst = self.tables.goto_next_of(self.eth_dst_table)

        msgs += [self.make_message (dp, self.ip_dstT_id, PRIORITY_MIN+2, match_ip, inst, actions)]
        print('Def msgs for ', dp)
        # for m in msgs:
        #     print(m)
        return msgs


    def del_def_gw_flow(self, dp, gw_with_netmask, gw_mac, neigh_mac):
        match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_dst = gw_with_netmask)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER)]
        msgs = self.del_flow(dp, self.ip_dstT_id, match_ip, priority=PRIORITY_MIN+5)

        match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src = gw_with_netmask)
        # actions = [parser.OFPActionPopVlan()]
        actions = [parser.OFPActionSetField(eth_src = gw_mac)]
        actions += [parser.OFPActionSetField(eth_dst = neigh_mac)]
        msgs += self.del_flow(dp, self.ip_dstT_id, match_ip, priority=PRIORITY_MIN+2, actions=actions)
        return msgs


    def change_neigh_mac(self, dp, new_mac, neigh_ip):
        # TODO Test
        self.neighours_mac[dp.id][neigh_ip] = new_mac
        # если новый мак соседа, переустанваливаем потоки шлюза по умолчанию
        # создаем новые
        new_msgs = self.set_default_flows(dp, new_mac, neigh_ip)
        if not bool(new_msgs):
            # если не получилось установить потоки по умолчанию - не отправляем hello сообщений
            # проверяем активных соседей этого свитча по dead intevalу
            print('Msgs for set_default_flows is empty')
            self.check_dead_neighbours(dp.id)
            return False
        # по идее старые удалять нет смысла, тк если такие же gw уже есть - то хорошо. А про какие-то другие гв мы не знаем
        util.send_msgs(dp, new_msgs)
        return True


    def ospf_hello_handler(self, ev):
        msg = ev.msg
        in_port = msg.match['in_port']        
        dp = msg.datapath
        pkt = packet.Packet(array('B', ev.msg.data))

        eth_pkt = pkt.get_protocols(ethernet.ethernet)[0]
        ip_pkt = pkt.get_protocols(ipv4.ipv4)[0]
        # проверяем, пришел ли пакет с тем же маком, если нет - обновляем данные о соседе
        # а если придет с другим айпи, то будет уже два айпи - это заготовка под возможность связать db больше чем с одним соседом
        neigh_mac = self.neighours_mac[dp.id].get(ip_pkt.src)
        if eth_pkt.src != neigh_mac or neigh_mac is None:
            print('!@@@@@@@@@@@@    Different source  ', eth_pkt.src, neigh_mac)
            # self.send_event_to_observers([c_ev.NeighMacChanged(dp.id, ip_pkt.src, eth_pkt.src)])
            if not self.change_neigh_mac(dp, new_mac = eth_pkt.src, neigh_ip = ip_pkt.src):
                return 

        # TODO delete if ospf app works
        # delete если метод change_neigh_mac будет оттестен и сработает норм
        # ставим потоки по умолчанию, если их еще нет
        # should_set = self.def_flows_not_set.get(dp.id)
        # if should_set or should_set is None:
        #     msgs = self.set_default_flows(dp, neigh_mac, ip_pkt.src)
        #     if not bool(msgs): #if msgs are empty
        #         # если не получилось установить потоки по умолчанию - не отправляем hello сообщений
        #         # проверяем активных соседей этого свитча по dead intevalу
        #         self.check_dead_neighbours(dp.id)
        #         print('Msgs is empty')
        #         return
        #     util.send_msgs(dp, msgs)
        #     self.def_flows_not_set[dp.id] = False
        # до сюда делет + self.def_flows_not_set не нужен

        # проверяем активных соседей этого свитча по dead intevalу
        self.check_dead_neighbours(dp.id)

        # отвечаем на hello сообщение
        ospfP = pkt.get_protocol(ospf.OSPFHello)
        # проверяем, была ли установлена с роутером смежность
        rid = ospfP.router_id
        neighbors = self.rid_neighours.get(dp.id)
        if neighbors is None or rid not in neighbors:
            self.rid_neighours[dp.id].append([rid, time.time()])
            # self.dbd_acks[dp.id] = 0
            self.drs[dp.id] = str(ospfP.designated_router) #всегда считаем, что наш роутер - slave TODO а если это не так? а если свитч общается больше чем с одним роутером и эта строчка два раза сработает?
        # TODO слишком много времени занимает конструирование соседей
        ns = self.rid_neighours[dp.id]
        nlist = []
        for n in ns:
            nlist += [n[0]]
        self.ospf_hello(dp, ip_pkt.src, options = 0x02, out_port = in_port, neighbors = nlist)
        

    def ospf_dbd_handler(self, ev):
        dp = ev.msg.datapath
        in_port = ev.msg.match['in_port']   
        pkt = packet.Packet(array('B', ev.msg.data))
        ipP = pkt.get_protocol(ipv4.ipv4)
        ospfP = pkt.get_protocol(ospf.OSPFDBDesc)
        # print('!!!', ospfP)
        dbd_ack = self.dbd_acks.get(dp.id)
        if dbd_ack is None:
            self.dbd_acks[dp.id] = 0
            dbd_ack = 0
            
        neigh_mac = self.neighours_mac[dp.id][ipP.src]
        if dbd_ack == 0:
            #если пустой, значит отправился первый пакет DBD, в котором указан master bit, i bit и проч
            #  I-bit — Init bit. Значение бита равное 1, означает, что этот пакет первый в последовательности DBD-пакетов
            # M-bit — More bit. Значение бита равное 1, означает, что далее последуют дополнительные DBD-пакеты 
            seq_num = ospfP.sequence_number-104
            self.ospf_advertise(dp = dp, ip_dst = ipP.src, mac_dst = neigh_mac, dd = seq_num, m_flag = 1, i_flag = 1, ms_flag = 1, options = 0x02, out_port = in_port)
            self.dbd_acks[dp.id] = 1
        elif dbd_ack == 1:
            #шлем пакет, притворяясь слейвом. В пакете все lsa маршруты и флаг more
            # тип линка - обязательно не СТАБ!! иначе маршруты из других зон не пройдут + надо еще слать флаг 0х03 чтобы быть и ASBR и BR
            #  инфу о линке возьмем из пакета
            heads = []
            dp_conf = self.net_config.dps[dp.id]
            gw_ip, bitmask, gw_mac = self.get_gw_info_for_ip(dp.id, ipP.src)
            if self.seq.get(dp.id) is None:
                self.seq[dp.id] = 0x8000000c
            # А если gw_ip == None?
            gw_ip = str(gw_ip)

            link2 = ospf.RouterLSA.Link(id_=ipP.src, data=gw_ip, type_=ospf.LSA_LINK_TYPE_TRANSIT, metric=1)
            lsa = ospf.RouterLSA(id_=self.route_id, adv_router=self.route_id, links=[link2], ls_seqnum=self.seq[dp.id], options = 0x22, flags=0x03)
            self.save_lsa(dp.id, lsa)
            heads = [lsa.header]

            # добавим также network 2 пакет
            # lsa = ospf.NetworkLSA(id_=gw_ip, adv_router=self.drs[dp.id], ls_seqnum=self.seq[dp.id], options = 0x02, mask=bitmask, routers=[self.route_id, self.drs[dp.id] ])
            # self.save_lsa(dp.id, lsa)
            # heads += [lsa.header]

            for inter in dp_conf.announced_gws.values():
                # пример объявления суммирующейся сетки из другой области
                # lsa = ospf.SummaryLSA(id_=str(inter.ip), adv_router=self.route_id, mask=str(inter.network.netmask), metric = 1, ls_seqnum=self.seq, options = 0x22)
                # объявляем внешние маршруты
                # TODO Метрика всегда 20. Стоит, наверное, менять
                net = ospf.ASExternalLSA.ExternalNetwork(mask=str(inter.network.netmask), metric=20, fwd_addr='0.0.0.0')
                lsa = ospf.ASExternalLSA( id_=str(inter.ip), extnws=[net], adv_router=self.route_id, ls_seqnum=self.seq[dp.id], options = 0x20)
                heads += [lsa.header]
                self.save_lsa(dp.id, lsa)

            self.ospf_advertise(dp = dp, ip_dst = ipP.src, mac_dst = neigh_mac, dd = ospfP.sequence_number, lsa_headers = heads, m_flag = 1, options = 0x02, out_port = in_port, mac_src = gw_mac, ip_src=gw_ip)
            self.dbd_acks[dp.id] = 2
        else:
            self.ospf_advertise(dp = dp, ip_dst = ipP.src, mac_dst = neigh_mac, dd = ospfP.sequence_number, options = 0x02)
        

    def ospf_upd_handler(self, ev):
        #update от других машрутизаторов, просто подтверждаем их
        dp = ev.msg.datapath
        pkt = packet.Packet(array('B', ev.msg.data))
        ospfP = pkt.get_protocol(ospf.OSPFLSUpd)
        acks = []
        for lsa in ospfP.lsas:
            #по хорошему - запоминаем этот lsa где-то у себя
            #но мы этого щас не делаем, просто отправляем подтверждение
            acks += [lsa.header]
        # print('!Update handler = ', acks)
        # print('\n', ospfP.lsas)
        if acks != []:
            ipP = pkt.get_protocol(ipv4.ipv4)
            neigh_mac = self.neighours_mac[dp.id][ipP.src]
            self.ospf_lsack(dp = dp, ip_dst = ipP.src, mac_dst = neigh_mac,  headers = acks)


    def ospf_req_handler(self, ev):
        #если приходит запрос определнного/ых lsu, которые мы отправляли
        dp = ev.msg.datapath
        pkt = packet.Packet(array('B', ev.msg.data))
        ipP = pkt.get_protocol(ipv4.ipv4)
        ospfP = pkt.get_protocol(ospf.OSPFLSReq)
        #приходит вот такое поле lsa_requests=[Request(adv_router='0.0.0.1',id='192.168.3.0',type_=3), Request(adv_router='0.0.0.1',id='192.168.5.0',type_=3)],
        temp_lsa = []
        for req in ospfP.lsa_requests:
            #отправить lsa, найдя его по lsa header
            lsa = self.find_lsa(dp.id, req)
            if lsa is not None:
                temp_lsa +=[lsa]
        neigh_mac = self.neighours_mac[dp.id][ipP.src]
        self.ospf_upd(dp, ip_dst = ipP.src, mac_dst = neigh_mac, lsas = temp_lsa)
        

    def find_lsa(self, dpid, req):
        # find lsa for lsa request
        for lsa in self.lsas[dpid]:
            head = lsa.header
            if head.adv_router == req.adv_router and head.id_ == req.id and head.type_ == req.type_:
                return lsa
        return None


    def ospf_ack_handler(self, ev):
        #при подтверждении от других роутеров (пока) ничего не делаем
        dp = ev.msg.datapath
        pkt = packet.Packet(array('B', ev.msg.data))
        ipP = pkt.get_protocol(ipv4.ipv4)
        
        dbd_ack = self.dbd_acks.get(dp.id)
        if dbd_ack is None:
            self.dbd_acks[dp.id] = 0
            dbd_ack = 0

        if dbd_ack == 1:
            neigh_mac = self.neighours_mac[dp.id][ipP.src]
            gw_ip, bitmask, gw_mac = self.get_gw_info_for_ip(dp.id, ipP.src)
            gw_ip = str(gw_ip)
            link = ospf.RouterLSA.Link(id_=self.drs[dp.id], data=gw_ip, type_=ospf.LSA_LINK_TYPE_TRANSIT, metric=10)
            lsa = ospf.RouterLSA(id_=self.route_id, adv_router=self.route_id, links=[link])
            self.ospf_upd(dp, mac_src=gw_mac, ip_src=gw_ip, ip_dst = ipP.src, mac_dst = neigh_mac, lsas = [lsa])
            self.save_lsa(dp.id, lsa)
            self.dbd_acks[dp.id] = 2
        
        # ospfP = pkt.get_protocol(ospf.OSPFLSAck)
        # чтобы проверять, все ли маршруты, которые мы заанонсили, были подтверждены роутерами
        # if ospfP.lsa_headers in self.dbd_lacks:
        #     удалить lsa header из self.dbd_lacks
        #     self.dbd_lacks.pop(spfP.lsa_headers)
        # если
        #     self.dbd_lacks пустой
        #     self.dbd_ack = False
        # иначе
        #     продолжаем анонсить маршруты


    def arp_handler(self, ev):
        pkt = packet.Packet(array('B', ev.msg.data))
        arpP = pkt.get_protocol(arp.arp)
        # arp запрос будет обработан l3 модулем
        # поэтому  просто запоминаем мак соседа
        dp = ev.msg.datapath
        neigh_mac = self.neighours_mac[dp.id].get(arpP.src_ip)
        if arpP.src_ip != neigh_mac or neigh_mac is None:
            print('!@@@@@@@@@@@@    Different source from Arp ', arpP.src_ip, neigh_mac)
            # self.send_event_to_observers([c_ev.NeighMacChanged(dp.id, arpP.src_ip, arpP.src_mac)])
            self.change_neigh_mac(dp, new_mac = arpP.src_mac, neigh_ip = arpP.src_ip)
        

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        pkt = packet.Packet(array('B', ev.msg.data))
        dp = ev.msg.datapath
        try:
            dp_conf = self.net_config.dps.get(dp.id)
        except Exception as xuita:
            print(xuita,'\nself.net_config = ', self.net_config)
            print('self.net_config.dps = ', self.net_config.dps)
        if dp_conf is None:
            return
        in_port = ev.msg.match['in_port']

        # если граничный свитч и сообщения пришли от роутера-соседа
        if dp_conf.ospf_out is not None and in_port == dp_conf.ospf_out:
            eth_pkt = pkt.get_protocols(ethernet.ethernet)[0]
            eth_type = eth_pkt.ethertype
            if eth_type != 33024 and eth_type!= 0x800 and eth_type != 0x806:
                # обрабатываем арп и ospf пакеты
                return
            #обрабатываем ethertype запакованный во влан пакеты
            if eth_type == 33024:
                vlan_pkt = pkt.get_protocols(vlan.vlan)[0]
                eth_type = vlan_pkt.ethertype
                if eth_type!= 0x800 and eth_type != 0x806:
                    # ignore not ipv4 (ospf) or arp packets
                    return

            if ospf.OSPFHello in pkt:
                self.ospf_hello_handler(ev)
            elif ospf.OSPFDBDesc in pkt:
                self.ospf_dbd_handler(ev)
            elif ospf.OSPFLSUpd in pkt:
                self.ospf_upd_handler(ev)
            elif ospf.OSPFLSAck in pkt:
                self.ospf_ack_handler(ev)
            elif ospf.OSPFLSReq in pkt:
                self.ospf_req_handler(ev)
            elif arp.arp in pkt:
                self.arp_handler(ev)
            # else:
            #     print('!!!!packet == ', pkt)


    def check_dead_neighbours(self, dpid):
        # проверка того, был ли активен свитч сосед в течение DEAD_Interval. Проверка идет по деад времени
        curtime = time.time()
        nlist = self.rid_neighours[dpid]
        for neigh in nlist:
            i = 0
            if neigh[1] + DEAD_INTERVAL < curtime:
                nlist.pop(i) #TODO need test, if pop is correct
                # # очищаем также информацию о ВСЕХ маках соседа
                # del self.neigh_macs[dpid]
            else:
                #тк элементы при удалении сдвигаются, то увеличивать счетчик надо только, если элем не удален
                i += 1
        # print('Neigh = ', self.rid_neighours[dpid]) для теста


    def save_lsa(self, dp_id, lsa):
        saved_lsa = self.lsas[dp_id]
        if lsa not in saved_lsa:
            # TODO lsa header содержит seq_num, тк seq может меняться, то следует в lsa оставлять только самый последний seq
            saved_lsa.append(lsa)


    def ospf_upd(self, dp, ip_dst, mac_dst, lsas,  mac_src=None, ip_src=None):
        if mac_src is None or ip_src is None:
            ip_src, bitmask, mac_src = self.get_gw_info_for_ip(dp.id, ip_dst)
        if ip_src is None:
            return
        out_port = self.net_config.dps[dp.id].ospf_out
        ospf_util.ospf_upd(dp = dp, mac_src=mac_src, ip_src=ip_src, router_id = self.route_id, out_ports = [out_port], area_id = self.area, ip_dst = ip_dst, mac_dst = mac_dst, lsas = lsas)


    def ospf_lsack(self, dp, ip_dst, mac_dst, headers):
        gw_ip, bitmask, gw_mac = self.get_gw_info_for_ip(dp.id, ip_dst)
        if gw_ip is None:
            return
        gw_ip = str(gw_ip)
        out_port = self.net_config.dps[dp.id].ospf_out
        ospf_util.ospf_lsack(dp = dp, mac_src=gw_mac, ip_src=gw_ip, router_id = self.route_id, out_ports = [out_port], area_id = self.area, ip_dst = ip_dst, mac_dst = mac_dst, headers = headers)


    def ospf_advertise(self, dp, ip_dst, mac_dst, dd = 0, lsa_headers = None, m_flag = 0, i_flag = 0, ms_flag = 0, options = 0x0, out_port = None, mac_src = None, ip_src=None):
        # sends ospf adverise nets msg
        if mac_src is None or ip_src is None:
            ip_src, bitmask, mac_src = self.get_gw_info_for_ip(dp.id, ip_dst)
        if ip_src is None:
            return
        if out_port is None:
            out_port = self.net_config.dps[dp.id].ospf_out
        ospf_util.ospf_advertise(dp = dp, mac_src = mac_src, ip_src=ip_src, router_id = self.route_id, out_ports = [out_port], area_id = self.area, ip_dst = ip_dst, m_flag=m_flag, mac_dst = mac_dst, dd = dd, lsa_headers = lsa_headers, options = options)


    def ospf_hello(self, dp, neigh_ip, options, neighbors, out_port = None):
        # sends ospf hello msg
        gw_ip, bitmask, gw_mac = self.get_gw_info_for_ip(dp.id, neigh_ip)
        if gw_ip is None:
            return
        if out_port is None:
            out_port = self.net_config.dps[dp.id].ospf_out
        # route_id и area_id для всех бордер свитчей одинаковы
        gw_ip = str(gw_ip)
        ospf_util.ospf_hello(dp = dp, options = options, mac_src=gw_mac, ip_src=gw_ip, designated_router = self.drs[dp.id], router_id=self.route_id, neighbors=neighbors, out_ports = [out_port], mask = bitmask, area_id = self.area) #backup_router = gw_ip

        # print('Ospf hello ', dp, options, gw_mac, gw_ip, self.drs[dp.id], self.route_id, neighbors, [out_port], bitmask, self.area)


    def get_gw_info_for_ip(self, dpid, ip):
        # get gateway mac for this ip
        ip_adr = ip_address(ip)
        dp = self.net_config.dps[dpid]
        # print('OSPF Get ip info = ', dpid, ip, dp.other_gws.items())
        for mac, sw_gw in dp.other_gws.items():
            gw = ip_interface(sw_gw)
            if ip_adr in gw.network:
                mask = str(gw.netmask)
                # print('!@@@@@@@@  net mask of ', gw,' is ', mask)
                return gw.ip, mask, mac
        # проверяем только others_gw, тк шлюз для стыковочного линка по идее не будет объявляться по ospf как внешний маршрут
        return None, None, None


    def clean_all_flows(self, dp):
        "Remove all flows with cookie from all tables"
        msgs = []
        for t in self.tables.tables:
            i = self.tables.table_id(t.name)
            msgs += [self.del_flow (dp, i)]
        msgs += util.barrier_request(dp)
        return msgs

    def make_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        return util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)

    def del_flow(self, dp, table_id = None, match = None, out_port=None, out_group=None, priority=32768, actions = None):
        return util.del_flow(dp, self.cookie, table_id, match, out_port, out_group, priority, actions)