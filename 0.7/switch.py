# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib.ofp_pktinfilter import packet_in_filter, RequiredTypeFilter
from ryu.ofproto import ofproto_v1_3
from ryu.ofproto import ofproto_v1_3_parser as parser
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, ipv6
from array import array

import helper_methods as util
from cache import HostCache
from ospf_util import add_flow
import table
import ofp_custom_events as c_ev


PRIORITY_MIN = 0
PRIORITY_DEF = 16000
PRIORITY_MAX = 32000
HARD_TIME = 40000
IDLE_TIME = 20000

cache_live_time = 1

class SimpleSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        'tables': table.Tables
        }

    def __init__(self, *args, **kwargs):
        super(SimpleSwitch13, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.host_cache = HostCache(cache_live_time)
        self.cookie = 1
        # self.acl_table, self.aclT_id = self.tables.get_table('acl')
        self.eth_src_table, self.eth_srcT_id = self.tables.get_table('eth_src')
        self.eth_dst_table, self.eth_dstT_id = self.tables.get_table('eth_dst')
        self.flood_table, self.floodT_id = self.tables.get_table('flood')


    @set_ev_cls(c_ev.NewDp, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.dp
        msgs = self.add_new_datapath(dp)
        util.send_msgs(dp, msgs)


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        dp = ev.msg.datapath
        in_port = ev.msg.match['in_port']
        # Parse the packet
        pkt = packet.Packet(array('B', ev.msg.data))
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return
        ip6 = pkt.get_protocol(ipv6.ipv6)
        if ip6 is not None:
            # ignore ipv6 packet
            return

        # Ensure this host was not recently learned to avoid flooding the switch
        # with the learning messages if the learning was already in process.
        if not self.host_cache.is_new_host(dp.id, in_port, eth.src):
            # print('Host %s is learned in port %s dp=%s' % (eth.src, in_port, dp.id) )
            return
        
        msgs = self.learn_source( dp=dp, port=in_port, eth_src=eth.src)
        util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.NewPredFlows_part2, MAIN_DISPATCHER)
    def _install_predict_flows(self, ev):
        # print(f'learn source port_num={ev.port_num} mac_src={ev.mac_src}')
        msgs = self.learn_source(ev.dp_obj, ev.port_num, ev.mac_src)
        util.send_msgs(ev.dp_obj, msgs)


    ## Instance Helper Methods
    def add_new_datapath(self, dp):
        "Add the specified datapath to our app by adding default rules"
        msgs = self.clean_all_flows(dp)
        msgs = self.add_default_flows(dp)
        return msgs
    
    def learn_source(self, dp, port, eth_src):
        "Learn the port associated with the source MAC"

        msgs = self.unlearn_source(dp, eth_src)

        #"Add flow to mark the source learned at a specific port"
        match = parser.OFPMatch(eth_src=eth_src, in_port=port)
        inst = self.tables.goto_next_of(self.eth_src_table)
        msgs = [self.make_message (dp, self.eth_srcT_id, PRIORITY_DEF, match, inst, hard_timeout=HARD_TIME)]

        #"Add flow to forward packet sent to eth_dst to out_port"
        match = parser.OFPMatch(eth_dst=eth_src)
        inst = [parser.OFPInstructionWriteMetadata(metadata = port, metadata_mask = 0xFFFFFFFF)]
        inst += self.tables.goto_next_of(self.eth_dst_table)
        msgs += [self.make_message (dp, self.eth_dstT_id, PRIORITY_DEF, match, inst, idle_timeout=IDLE_TIME)]
       
        #write this port to flood
        match = parser.OFPMatch(metadata=port)
        actions = [parser.OFPActionOutput(port)]
        msgs += [self.make_message (dp, self.floodT_id, PRIORITY_DEF, match, actions = actions, idle_timeout=IDLE_TIME)]
        
        return msgs


    def unlearn_source(self, dp, eth_src):
        "Remove any existing flow entries for this MAC address"
        msgs = [self.del_flow(dp, self.eth_srcT_id, match = parser.OFPMatch(eth_src=eth_src))]
        msgs += [self.del_flow(dp, self.eth_dstT_id, match = parser.OFPMatch(eth_dst=eth_src))]
        msgs += util.barrier_request(dp)
        return msgs


    def clean_all_flows(self, dp):
        "Remove all flows with the Simple switch cookie from all tables"
        msgs = []
        for t in self.tables.tables:
            i = self.tables.table_id(t.name)
            msgs += [self.del_flow (dp, i)]
        return msgs


    def add_default_flows(self, dp):
        "Add the default flows needed for this environment"
        # print (0x3 & ~0xF) | (value & mask)
        ofp = dp.ofproto
        msgs = []
        ## TABLE_ACL
        # Add a low priority table-miss flow to forward to the switch table.
        # Other modules can add higher priority flows as needed.
        # inst = self.tables.goto_next_of(self.acl_table)
        # msgs += [self.make_message (dp, self.aclT_id, PRIORITY_MIN, parser.OFPMatch(), inst)]

        ## TABLE_L2_SWITCH
        # Drop certain packets that should not be broadcast or processed
        def _drop(match, priority = PRIORITY_MAX):
            "Helper to create a drop flow entry for table_l2_switch"
            return [self.make_message (dp, self.eth_srcT_id, priority, match)]

        # Drop LLDP
        msgs += _drop(parser.OFPMatch(eth_type=ether_types.ETH_TYPE_LLDP))
        # Drop STDP BPDU
        msgs += _drop(parser.OFPMatch(eth_dst='01:80:c2:00:00:00'))
        msgs += _drop(parser.OFPMatch(eth_dst='01:00:0c:cc:cc:cd'))
        # Drop Broadcast Sources
        msgs += _drop(parser.OFPMatch(eth_src='ff:ff:ff:ff:ff:ff'))
        # IF now match - drop packet
        msgs += _drop(parser.OFPMatch(), PRIORITY_MIN)

        ## TABLE_ETH_SRC
        # if dont found in table -> send to controller and send to TABLE_ETH_DST
        # We send to TABLE_ETH_DST because the SRC rules will hard timeout
        # before the DST rules idle timeout. This gives a last chance to
        # prevent a flood event while the controller relearns the address.
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, max_len=256)]
        inst = self.tables.goto_next_of(self.eth_src_table)
        # inst = self.tables.goto_this(self.eth_dstT_id) #чтобы перескакивать таблицы между eth src и eth dst
        msgs += [self.make_message (dp, self.eth_srcT_id, PRIORITY_MIN, parser.OFPMatch(), inst, actions)]

        # TABLE_ETH_DST
        #переходить во flood table, if no match
        inst = self.tables.goto_next_of(self.eth_dst_table)
        msgs += [self.make_message (dp, self.eth_dstT_id, PRIORITY_MIN, parser.OFPMatch(), inst)]
         
        #TABLE_FLOOD
        #если vlan app активирован, то это можно убрать
        # # Flood multicast (Mimic Faucet)
        # flood_addrs = [
        #     ('01:80:c2:00:00:00', '01:80:c2:00:00:00'), # 802.x
        #     ('01:00:5e:00:00:00', 'ff:ff:ff:00:00:00'), # IPv4 multicast
        #     ('33:33:00:00:00:00', 'ff:ff:00:00:00:00'), # IPv6 multicast
        # ]
        # actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        # for eth_dst in flood_addrs:
        #     match = parser.OFPMatch(eth_dst=eth_dst)
        #     msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MIN, match, actions = actions)]

        # Ethernet broadcast
        #выключен тк включен модуль Vlan
        # match = parser.OFPMatch(eth_dst='ff:ff:ff:ff:ff:ff')
        # msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MIN, match, actions = actions)]

        # if nothing in table found - не flood
        # msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MIN, parser.OFPMatch(), actions = actions)]

        return msgs


    def make_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        return util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)

        
    def del_flow(self, dp, table_id = None, match = None, out_port=None, out_group=None):
        return util.del_flow(dp, self.cookie, table_id, match, out_port, out_group)