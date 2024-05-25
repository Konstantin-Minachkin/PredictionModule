# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib.ofp_pktinfilter import packet_in_filter, RequiredTypeFilter
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from array import array

from config import Config
import threading
import time

class Lldp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        'net-config': Config
        }

    def __init__(self, *args, **kwargs):
        super(Lldp, self).__init__(*args, **kwargs)
        self.net_config = kwargs['net-config']
        self.hello_interval = 10
        self.dead_interval = self.hello_interval * 4
    #     background = threading.Thread(target=self.back_run, args = (self.net_config.active_dps))

    # def back_run(self, dps):
    #     """ Method that runs forever and create lldp frame for all switches in config"""
    #     # dps = self.net_config.active_dps
    #     while True:
    #         for d in dps:
    #             dp = d.dp_obj
    #             #create lldp frame
                
    #         time.sleep(self.hello_interval)


    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, CONFIG_DISPATCHER)
    def port_desc_stats_reply_handler(self, ev):
        print('!!!!LLDP app works too')
        # dp = ev.msg.datapath
        # #узнаем, какие у dp есть порты
        # self.net_config.ports_info_for_dp[dp.id] = ev.msg.body
        # #register switch
        # events = self.net_config.register_dp(dp)
        # for ev in events:
        #     self.send_event_to_observers(ev)

    
        