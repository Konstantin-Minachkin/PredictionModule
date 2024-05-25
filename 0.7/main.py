# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib.ofp_pktinfilter import packet_in_filter, RequiredTypeFilter
from ryu.ofproto import ofproto_v1_3
from ryu.ofproto import ofproto_v1_3_parser as parser
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4

import helper_methods as util
from cache import HostCache
from ospf_util import add_flow
import table

import ofp_custom_events as c_ev
from config import Config


#from faucet.valve_of import output_controller


class MainApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        'tables': table.Tables,
        'net-config': Config
        }

    _EVENTS = [c_ev.NewDp, c_ev.LostDp,
        c_ev.StartBackground,
        c_ev.NewPortNativeVlan, c_ev.NewPortTaggedVlan,
        c_ev.PortUp, c_ev.PortDown,
        c_ev.NewAnnouncedGw, c_ev.NewGw, c_ev.DelAnnouncedGw, c_ev.DelGw,
        c_ev.NewBorderRouter, c_ev.DelBorderRouter,
        c_ev.PortStateChanged,
        c_ev.AclInChanged, c_ev.AclOutChanged, c_ev.AclChanged, c_ev.DelAcl, c_ev.NewAcl,
        c_ev.VlanChanged]

    def __init__(self, *args, **kwargs):
        super(MainApp, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.net_config = kwargs['net-config']
        self.back_is_started = False

        #Adding tables for an app
        vl_in = table.Table('vl_in')
        acl_in = table.Table('acl_in')
        eth_src = table.Table('eth_src')
        prediction_log = table.Table('prediction_log')
        ip_dst = table.Table('ip_dst')
        eth_dst = table.Table('eth_dst')
        vl_change = table.Table('vl_change')
        acl_out = table.Table('acl_out')
        vl_out = table.Table('vl_out')
        flood = table.Table('flood')

        self.tables.add_table(vl_in)
        self.tables.add_table(acl_in, 'vl_in')
        self.tables.add_table(eth_src, 'acl_in')
        self.tables.add_table(prediction_log, 'eth_src')
        self.tables.add_table(ip_dst, 'prediction_log')
        self.tables.add_table(eth_dst, 'ip_dst')
        self.tables.add_table(vl_change, 'eth_dst')
        self.tables.add_table(acl_out, 'vl_change')
        self.tables.add_table(vl_out, 'acl_out')
        self.tables.add_table(flood, 'vl_out')


    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, CONFIG_DISPATCHER)
    def port_desc_stats_reply_handler(self, ev):
        # print('@@@@@@@@switch_reply', ev.msg.body)
        dp = ev.msg.datapath
        #register switch
        events = self.net_config.register_dp(dp, ev.msg.body)
        for ev in events:
            self.send_event_to_observers(ev)


    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def new_switch_handler(self, ev):
        # dp = ev.msg.datapath
        #start background app
        if not self.back_is_started:
            self.back_is_started = True
            self.send_event_to_observers(c_ev.StartBackground())
            

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def del_switch_handler(self, ev):
        dp = ev.datapath
        events = self.net_config.unregister_dp(dp)
        for ev in events:
            self.send_event_to_observers(ev)


    @set_ev_cls(c_ev.PortUp, MAIN_DISPATCHER)
    def port_up_handler(self, ev):
        dp = ev.dp.dp_obj
        util.send_msgs(dp, util.port_up(ev.port, dp))
        #Шлем запрос на получение конфигурации портов
        dp.send_msg(parser.OFPPortDescStatsRequest(dp, 0))


    @set_ev_cls(c_ev.PortDown, MAIN_DISPATCHER)
    def port_down_handler(self, ev):
        util.send_msgs(ev.dp.dp_obj, util.port_shut(ev.port, ev.dp.dp_obj))
