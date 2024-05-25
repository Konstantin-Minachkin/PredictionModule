# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3 as ofproto
from ryu.ofproto import ofproto_v1_3_parser as parser
from ryu.lib.ofp_pktinfilter import packet_in_filter, RequiredTypeFilter
from ryu.lib.packet import packet as ryu_pck, ethernet, ipv4, vlan, tcp, udp

from ryu.topology import event
from collections import defaultdict
from array import array
import table
from ipaddress import ip_interface, ip_network, ip_address
import datetime, pika, yaml

import helper_methods as util

import ofp_custom_events as c_ev

# только в рамках облегчения разработки
import sys
sys.path.append('/home/control/Documents/PredictionService/')
# в финальной версии - строчки выше должны быть убраны, а папка Datatypes в директории с этим кодом - обновлена
from Datatypes import Package_pb2
from Datatypes.LoggedDps import LoggedDps

from cache import HostCache




CONFIG_PATH_PREDICTION_SERVICE = 'settings.yml'
time_to_make_way = 10
packet_barrier_timeout = 1*time_to_make_way #сколько секунд не пускать пакет, пришедший с того же ip_dst+ip_src+port_type+port_num
eth_barrier_timeout = 1*time_to_make_way #сколько секунд не пускать пакет, пришедший с того же dp+port+с тем же mac
lf_pkt_timeout = 1

class ConnectorToApp(app_manager.RyuApp):
    # connection app that sends smth to PredictionService

    OFP_VERSIONS = [ofproto.OFP_VERSION]
    _CONTEXTS = {
        'tables': table.Tables,
        'prediction-dps_with_logflows': LoggedDps
        }

    def __init__(self, *args, **kwargs):
        super(ConnectorToApp, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.dps_with_logflows = kwargs['prediction-dps_with_logflows'].dps
        self.log_table, self.log_table_id = self.tables.get_table('prediction_log')

        self.eth_host_cache = HostCache(eth_barrier_timeout)
        self.ip_host_cache = HostCache(packet_barrier_timeout)
        self.log_flag_host_cache = HostCache(lf_pkt_timeout)

        with open(CONFIG_PATH_PREDICTION_SERVICE) as f:
            config = yaml.safe_load(f)

        self.rbmq_conn_dict = {
            'credentials': pika.PlainCredentials(config["rabbitConnectionString"]["user"], config["rabbitConnectionString"]["passw"]),
            'host': config["rabbitConnectionString"]["host"],
            'port': config["rabbitConnectionString"]["port"],
            'virtual_host': config["rabbitConnectionString"]["vhost"],
            'heartbeat': 60,
            'stack_timeout': None
        }
        self.connection = pika.BlockingConnection(pika.ConnectionParameters(**self.rbmq_conn_dict))
        self.channel = self.connection.channel()

    # def destructor - self.connection.close() https://stackoverflow.com/questions/865115/how-do-i-correctly-clean-up-a-python-object непонятно, какой подход выбрать - можно потом подумать


    def decode_log_flag(self, cookie):
        # для номера серии выделим больше памяти, чем для номера пердсказания в серии
        # max log_num = 8 bit = 255
        # cookie_bits = 64
        # log_bits = 8
        log_num = cookie & 0x00000000000000FF
        ser_num = (cookie & 0xFFFFFFFFFFFFFF00) >> 8
        return int(ser_num), int(log_num)


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    # @set_ev_cls(c_ev.ForPredictionEventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, pack_ev):
        # ev = pack_ev.event
        ev = pack_ev

        pkt = ev.msg.data
        unserialezed_pkt = ryu_pck.Packet(array('B', pkt))

        eth_pkt = unserialezed_pkt.get_protocols(ethernet.ethernet)[0]
        eth_type = eth_pkt.ethertype 
        if eth_type != 33024 and eth_type!= 0x800:
            # ignore not vlan or ipv4 packets
            return
        
        # if vlan -> check eth_type for ipv4 packet
        vlan_pkt = unserialezed_pkt.get_protocols(vlan.vlan)
        if bool(vlan_pkt):
            vlan_pkt = vlan_pkt[0]
            eth_type = vlan_pkt.ethertype
            if eth_type!= 0x800:
                # ignore not ipv4
                return
        
        # чтобы обработать ситуацию, когда на контроллер приходит больше 1 пакета за раз (например, когда путь для пакетов еще не построен и они валятся на контроллер) - запоминаем их на время = packet_barrier_timeout
        dp = ev.msg.datapath
        in_port = ev.msg.match['in_port']

        # определяем лог флаг пакета. Лог флаг = куки моей кастомной таблицы PredictionsLog
        if ev.msg.reason is ofproto.OFPR_ACTION and ev.msg.table_id == self.log_table_id:
            #будем в нашей собственной табличке - куда никто больше не полезет - присваивать куки сообщений лог флаг
            ser_num, log_flag = self.decode_log_flag(ev.msg.cookie)
            have_lf = True
        else:
            ser_num = 0
            log_flag = 0
            have_lf = False

        #на сообщения с log-flag этот баръер тоже влияет по идее - поэтому проверяем have_lf, чтобы не влиял
        if not have_lf and not self.eth_host_cache.is_new_host(dp.id, in_port, eth_pkt.src):
            # print('Host %s is learned in port %s dp=%s' % (eth.src, in_port, dp.id) )
            return


        ip_pkt = unserialezed_pkt.get_protocols(ipv4.ipv4)[0]
        
        frame = unserialezed_pkt.get_protocols(tcp.tcp)
        ftype = 'none'
        if bool(frame):
            frame = frame[0]
            ftype = 'tcp'
        else:
            frame = unserialezed_pkt.get_protocols(udp.udp)
            if bool(frame):
                frame = frame[0]
                ftype = 'udp'
        
        fport_num = int(frame.dst_port) if bool(frame) else 0

        # если не tcp\udp - не обрабатываем пакет
        # если такой пакет уже приходил недавно - не пересылать его
        # пришло несколько пакетов в rabbit с одинаковым лог флагом вместо одного такого пакета - скорее всего из-за того, что на удаление лог-флоу надо время -поэтому ввел have_lf
        #на сообщения с log-flag этот баръер тоже влияет по идее - поэтому проверяем have_lf, чтобы лог флаг пакеты никак не учитывались
        if have_lf:
            if not self.log_flag_host_cache.is_new_host(ip_pkt.dst, ip_pkt.src, ev.msg.cookie):
                # если лог флаг пакет уже приходил недавно на контроллер - не отправлять повторно
                return
        elif not self.ip_host_cache.is_new_host(ip_pkt.dst, ip_pkt.src, f'{ftype}:{fport_num}'):
            return

        # парсим пакет и передаем нужные поля через rabbit сервису предсказаний
        packet = Package_pb2.Package()
        packet.ip_src = ip_pkt.src
        packet.ip_dst = ip_pkt.dst
        packet.port_type = Package_pb2.portType.Value(ftype)
        packet.port = fport_num
        packet.timestap = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        packet.log_flag = log_flag
        packet.log_flag_series_num = ser_num

        try:
            self.channel.basic_publish( exchange='prediction_requests', routing_key='save_pkt', 
                        properties=pika.BasicProperties(headers={"proto":True}), 
                        body=packet.SerializeToString(packet))
        except (pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError, pika.exceptions.AMQPHeartbeatTimeout) as er:
            print (f'error while publish to rabbit {er}')
            self.connection = pika.BlockingConnection(pika.ConnectionParameters(**self.rbmq_conn_dict))
            self.channel = self.connection.channel()
            self.channel.basic_publish( exchange='prediction_requests', routing_key='save_pkt', 
                        properties=pika.BasicProperties(headers={"proto":True}), 
                        body=packet.SerializeToString(packet))

        # при получении пакета - флоу, который подходит для этого пакета надо удалить со свитча - тк лог флоу должен только один раз отработать
        # удалять флоу надо сразу со всех устроств, где он есть - не только с одного dp
        dv = self.dps_with_logflows.get(packet.ip_dst)
        if dv is not None:
            sv = dv.get(packet.ip_src)
            if sv is not None:
                for dp in sv:
                    match_ip = parser.OFPMatch(eth_type=0x0800, ipv4_src=packet.ip_src, ipv4_dst=packet.ip_dst)
                    del_msg = parser.OFPFlowMod(datapath=dp, cookie=ev.msg.cookie, cookie_mask=0xFFFFFFFFFFFFFFFF, table_id=self.log_table_id, command=ofproto.OFPFC_DELETE, out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY, match = match_ip)
                    #формируем итоговое сообщение
                    tmp_msgs = [del_msg, dp.ofproto_parser.OFPBarrierRequest(datapath=dp)]
                    util.send_msgs(dp, tmp_msgs)
                self.dps_with_logflows[packet.ip_dst].pop(packet.ip_src)