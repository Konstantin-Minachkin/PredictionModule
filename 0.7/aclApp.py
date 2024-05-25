# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3 as ofproto
from ryu.ofproto import ofproto_v1_3_parser as parser

from collections import defaultdict
import helper_methods as util
import ofp_custom_events as c_ev
from config import Config
import random
from table import Tables

from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, vlan
from array import array
from ryu.lib import ofctl_v1_3 as ofctl

ACL_IN = 1
ACL_OUT = 2

PRIORITY_MIN = 0
PRIORITY_DEF = 16000
PRIORITY_MAX = 32000

# use True to set flows with priority min, that allows all traffic, that doesnt match to acl rules, to go to next tables
ALLOW_OTHER_TRAFFIC = True 


class AclApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto.OFP_VERSION]

    _CONTEXTS = {
        'tables': Tables,
        'net-config': Config
        }

    _EVENTS = [ c_ev.getVlantoPorts ]

    def __init__(self, *args, **kwargs):
        super(AclApp, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.net_config = kwargs['net-config']
        self.cookie = 60
        self.acl_in_table, self.acl_inT_id = self.tables.get_table('acl_in')
        self.acl_out_table, self.acl_outT_id = self.tables.get_table('acl_out')
    
    
    @set_ev_cls(c_ev.NewDp, MAIN_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.dp
        msgs = self.clean_all_flows(dp)
        ev = c_ev.getVlantoPorts(dp, code = 1)
        util.send_msgs(dp, msgs)
        self.send_event_to_observers(ev)
        

    @set_ev_cls(c_ev.giveVlantoPorts, MAIN_DISPATCHER)
    def vlan_acls_install_handler(self, ev):
        dp = ev.dp
        code = ev.code
        vl_to_ports = ev.vlan_to_ports
        if code == 1:
            msgs = self.add_default_flows(dp, vl_to_ports)
        elif code == 2:
            msgs = []
            acl_name = ev.data.get('acl_name')
            self.change_acl_on_dps(vl_to_ports, acl_name)
        elif code == 3:
            port = ev.data.get('port')
            msgs = self.change_acl_on_port(dp, port, vl_to_ports)
        elif code == 4:
            msgs = []
            self.vl_changed_cont_handler(ev, vl_to_ports)
        util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.AclInChanged, MAIN_DISPATCHER)
    def _acl_in_changed(self, ev):
        dp = ev.dp
        port = ev.port
        # удаление acl потоков с порта
        for dp in self.net_config.active_dps.values():
            dp = dp.dp_obj
            self.del_acl_flow(dp, pnum = port.num, del_only = ACL_IN)
        # установка потоков заново
        msgs = self.port_to_acl_flows(dp, port, add_only = ACL_IN)
        util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.AclOutChanged, MAIN_DISPATCHER)
    def _acl_out_changed(self, ev):
        dp = ev.dp
        port = ev.port
        # удаление acl потоков с порта
        for dp in self.net_config.active_dps.values():
            dp = dp.dp_obj
            self.del_acl_flow(dp, pnum = port.num, del_only = ACL_OUT)
        # установка потоков заново
        msgs = self.port_to_acl_flows(dp, port, add_only = ACL_OUT)
        util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.NewPortTaggedVlan, MAIN_DISPATCHER)
    @set_ev_cls(c_ev.NewPortNativeVlan, MAIN_DISPATCHER)
    def _port_native_vlan_handler(self, ev):
        dp = ev.dp.dp_obj
        port = ev.port
        # удалить потоки старого влана (тк не знаем его, то все потоки) с порта
        self.del_acl_flow(dp, pnum = port.num)

        # заинсталить новые потоки на порт
        ev = c_ev.getVlantoPorts(dp, code = 3, data = { 'port':port })
        self.send_event_to_observers(ev)

    def change_acl_on_port(self, dp, port, vl_to_ports):
        #  установить на порт новые потоки
        acl_in_vlans = {} #{ vid: [ports] }
        acl_out_vlans = {} #{ vid: [ports] }
        vids = vl_to_ports.keys()
        vnames = self.get_vl_names(vids) # {vid : vname }

        # создаем список вланов, где есть acl #и был замечен этот порт и 
        for vid in vids:
            vname = vnames.get(vid)
            vl = self.net_config.vlans[vname]
            if vl.acl_in is not None and port.num:
                acl_in_vlans[vid] = vl_to_ports[vid]
            if vl.acl_out is not None:
                acl_out_vlans[vid] = vl_to_ports[vid]

        # ставим асл потоки на порт
        msgs = self.port_to_acl_flows(dp, port, acl_in_vlans, acl_out_vlans, vnames)
        util.send_msgs(dp, msgs)
        return msgs


    @set_ev_cls(c_ev.AclChanged, MAIN_DISPATCHER)
    def _acl_changed_handler(self, ev):
        self.send_event_to_observers(c_ev.getVlantoPorts(ev.dp, code = 2, data = { 'acl_name':ev.acl_name} ))
    
    def change_acl_on_dps(self, vl_to_ports, acl_name):
        acl_in_vlans = {} #{ vid: [ports] }
        acl_out_vlans = {} #{ vid: [ports] }
        vids = vl_to_ports.keys()
        vnames = self.get_vl_names(vids) # {vid : vname }

        # создаем список вланов, шде был замечен этот асл
        for vid in vids:
            vname = vnames.get(vid)
            vl = self.net_config.vlans[vname]
            if vl.acl_in is not None and acl_name in vl.acl_in:
                acl_in_vlans[vid] = vl_to_ports[vid]
            if vl.acl_out is not None and acl_name in vl.acl_out:
                acl_out_vlans[vid] = vl_to_ports[vid]
        
        def _find_vid(pnum, acl_vlans):
            vids = []
            for vid, ports in acl_vlans.items():
                if pnum in ports:
                    vid.append(vid)
            return vids

        # проверяем, на каких портах задан асл
        for dp_conf in self.net_config.active_dps.values():
            dp = dp_conf.dp_obj
            for port in dp_conf.ports.values():
                # работаем с асл на порту
                port_ch_in = False
                port_ch_out = False
                if port.acl_in is not None and port.acl_in == acl_name:
                    port_ch_in = True
                if port.acl_out is not None and port.acl_out == acl_name:
                    port_ch_out = True
                
                vl_ch_in = _find_vid(port.num, acl_in_vlans)
                if not bool(vl_ch_in):
                    vl_ch_in = False

                vl_ch_out = _find_vid(port.num, acl_out_vlans)
                if not bool(vl_ch_out):
                    vl_ch_out = False

                # delete old vlan flows
                if bool(vl_ch_in):
                    for vl in vl_ch_in:
                        self.del_acl_flow(dp, vid = vl, del_only = ACL_IN)

                if bool(vl_ch_out):
                    for vl in vl_ch_in:
                        self.del_acl_flow(dp, vid = vl, del_only = ACL_OUT)
                
                # delete old port flows
                if port_ch_in:
                    if port_ch_out:
                        self.del_acl_flow(dp, pnum = port.num)
                    else:
                        self.del_acl_flow(dp, pnum = port.num, del_only = ACL_IN)
                elif port_ch_out:
                        self.del_acl_flow(dp, pnum = port, del_only = ACL_OUT)

                # add new flows
                msgs = self.port_to_acl_flows(dp, port, acl_in_vlans, acl_out_vlans, vnames, a_in = port_ch_in, a_out = port_ch_out, v_in = bool(vl_ch_in), v_out = bool(vl_ch_out))
                util.send_msgs(dp, msgs)
                
    
    @set_ev_cls(c_ev.VlanChanged, MAIN_DISPATCHER)
    def _vl_changed_handler(self, ev):
        # if smthing in vlan has changed - check if it is acl
        old_vl = ev.old_vlan
        new_vl = ev.new_vlan

        vl_ch_in = False
        vl_ch_out = False
        if ( old_vl.acl_in is not None and new_vl.acl_in is None ) or ( new_vl.acl_in is not None and old_vl.acl_in is None) or ( set(old_vl.acl_in) != set(new_vl.acl_in) ):
            # have to delete acl_in for new_vl.vid and install new one
            vl_ch_in = True
        
        if ( old_vl.acl_out is not None and new_vl.acl_out is None ) or ( new_vl.acl_out is not None and old_vl.acl_out is None) or ( set(old_vl.acl_out) != set(new_vl.acl_out) ):
            # have to delete acl_out for new_vl.vid and install new one
            vl_ch_out = True
        
        if vl_ch_out or vl_ch_in:
            self.send_event_to_observers(c_ev.getVlantoPorts(new_vl.vid, code = 4, data = { 'vl_ch_in':vl_ch_in, 'vl_ch_out': vl_ch_out, 'new_vl': new_vl} ))
    

    def vl_changed_cont_handler(self, ev, vl_to_ports):
        vl_ch_in = ev.data.get('vl_ch_in')
        vl_ch_out = ev.data.get('vl_ch_out')
        new_vl = ev.data.get('new_vl')

        for dp_conf in self.net_config.active_dps.values():
            dp = dp_conf.dp_obj
            if vl_ch_in:
                if vl_ch_out:
                    self.del_acl_flow(dp, vid = new_vl.vid)
                else:
                    self.del_acl_flow(dp, vid = new_vl.vid, del_only = ACL_IN)
            elif vl_ch_out:
                self.del_acl_flow(dp, vid = new_vl.vid, del_only = ACL_OUT)

            acl_in_vlans = {} #{ vid: [ports] }
            acl_out_vlans = {} #{ vid: [ports] }
            vids = vl_to_ports[dp_conf.id].keys()
            vnames = self.get_vl_names(vids) # {vid : vname }

            # проверяем, относится ли порт ко влану, на котором задан асл
            for vid in vids:
                vname = vnames.get(vid)
                vl = self.net_config.vlans[vname]
                if vl.acl_in is not None:
                    acl_in_vlans[vid] = vl_to_ports[dp_conf.id][vid]
                if vl.acl_out is not None:
                    acl_out_vlans[vid] = vl_to_ports[dp_conf.id][vid]

            for port in dp_conf.ports.values():
                # add new flows
                msgs = self.port_to_acl_flows(dp, port, acl_in_vlans, acl_out_vlans, vnames, a_in = False, a_out = False, v_in = vl_ch_in, v_out = vl_ch_out)
                util.send_msgs(dp, msgs)


    # не обрабатываем, тк на порт нельзя задать acl, если acl не объявлен, поэтому будем ловить только Acl IN Out Changed события
    # @set_ev_cls(c_ev.NewAcl, MAIN_DISPATCHER)
    # @set_ev_cls(c_ev.DelAcl, MAIN_DISPATCHER)


    def add_default_flows(self, dp, vl_to_ports):
        msgs = []
        acl_in_vlans = {} #{ vid: [ports] }
        acl_out_vlans = {} #{ vid: [ports] }
        vids = vl_to_ports.keys()
        vnames = self.get_vl_names(vids) # {vid : vname }

        # проверяем, относится ли порт ко влану, на котором задан асл
        for vid in vids:
            vname = vnames.get(vid)
            vl = self.net_config.vlans[vname]
            if vl.acl_in is not None:
                acl_in_vlans[vid] = vl_to_ports[vid]
            if vl.acl_out is not None:
                acl_out_vlans[vid] = vl_to_ports[vid]

        # проверяем, есть ли на каждом порту dp асл - если да, применяем его
        dp_conf = self.net_config.dps[dp.id]
        for port in dp_conf.ports.values():
            msgs += self.port_to_acl_flows(dp, port, acl_in_vlans, acl_out_vlans, vnames)

        # весь остальной траффик пропускаем дальше
        if ALLOW_OTHER_TRAFFIC:
            match = parser.OFPMatch()
            inst = self.tables.goto_next_of(self.acl_in_table)
            msgs += [self.make_message (dp, self.acl_inT_id, PRIORITY_MIN, match, inst)]

            inst = self.tables.goto_next_of(self.acl_out_table)
            msgs += [self.make_message (dp, self.acl_outT_id, PRIORITY_MIN, match, inst)]

        return msgs


    def port_to_acl_flows(self, dp, port, acl_in_vlans = None, acl_out_vlans = None, vnames = None, add_only = -1, a_in = True, a_out = True, v_in = True, v_out = True):
        msgs = []
        if add_only != ACL_OUT:
            if a_in and port.acl_in is not None:
                # add port acl in rules
                for acl in port.acl_in:
                    acl = self.net_config.acls[acl]
                    msgs += self.acl_to_flows(acl, dp, port.num, ACL_IN)

            # проверяем, относится ли порт ко влану, на котором задан асл
            if v_in and acl_in_vlans is not None:
                for vid, ports in acl_in_vlans.items():
                    if port.num in ports:
                        vname = vnames.get(vid)
                        vl = self.net_config.vlans[vname]
                        # add vlan acl in rules
                        for acl in vl.acl_in:
                            acl = self.net_config.acls[acl]
                            msgs += self.acl_to_flows(acl, dp, port.num, ACL_IN, vlan = vid)

        if add_only != ACL_IN:
            if a_out and port.acl_out is not None:
                # add port acl out rules
                for acl in port.acl_out:
                    acl = self.net_config.acls[acl]
                    msgs += self.acl_to_flows(acl, dp, port.num, ACL_OUT)

            if v_out and acl_out_vlans is not None:
                for vid, ports in acl_out_vlans.items():
                    if port.num in ports:
                        vname = vnames.get(vid)
                        vl = self.net_config.vlans[vname]
                        # add vlan acl out rules
                        for acl in vl.acl_out:
                            acl = self.net_config.acls[acl]
                            msgs += self.acl_to_flows(acl, dp, port.num, ACL_OUT, vlan = vid)
        return msgs


    def acl_to_flows(self, acl, dp, pnum, atype, vlan = None):
        # Превращает acl в opf message
        msgs= []
        priority = PRIORITY_MAX - 100

        for rule in acl.rules:
            msgs+=self.rule_to_flow(rule, dp, pnum, atype, priority, vlan)
            if priority > PRIORITY_MIN+5:
                # если приоритет достигнет такого значения - запрещаем понижать приоритет, тк все что ниже будет использоваться для работы с PRIORITY_MIN траффиком
                priority -= 1
        return msgs

    
    def rule_to_flow(self, rule, dp, pnum, atype, priority, vid = None):
        if atype == ACL_IN:
            if rule.match is not None:
                match = rule.match
                match.update( {'in_port': pnum} )
                if vid is not None:
                    match.update( {'vlan_vid': vid} )
            else:
                match = {'in_port': pnum}
                if vid is not None:
                    match.update( {'vlan_vid': vid} )
            inst = self.tables.goto_next_of(self.acl_in_table)
            table_id = self.acl_inT_id
        elif atype == ACL_OUT:
            if rule.match is not None:
                match = rule.match
                match.update( {'metadata': pnum} )
                if vid is not None:
                    match.update( {'vlan_vid': vid} )
            else:
                match = {'metadata': pnum, 'eth_type': 33024 } #мэтч только по метадате не работает, поэтому мэтчим еще и по eth_type = 33024??
                if vid is not None:
                    match.update( {'vlan_vid': vid} )
            inst = self.tables.goto_next_of(self.acl_out_table)
            table_id = self.acl_outT_id
        else:
            return []

        # print('! match for ', dp.id, pnum, vid, match)
        match = ofctl.to_match(dp, match)
        allow = rule.actions.get('allow')
        if allow is None:
            return []
        elif allow:
            msg = [self.make_message (dp, table_id, priority, match, inst)]
        else:
            msg = [self.make_message (dp, table_id, priority, match)]

        return msg


    def get_vl_names(self, vids):
        # возваращет имена вланов для списка номеров вланов
        vl_names = {}
        for vl_name, vl in self.net_config.vlans.items():
            if vl.vid in vids:
                vl_names[vl.vid] = vl_name 
        return vl_names


    def del_acl_flow(self, dp, pnum = None, vid = None, del_only = None):
        # delete flows by port num or by vlan id
        if pnum is not None:
            match_in = parser.OFPMatch(in_port = pnum)
            match_out = parser.OFPMatch(metadata = pnum)
        elif vid is not None:
            match_in = parser.OFPMatch(vlan_vid = vid)
            match_out = parser.OFPMatch(vlan_vid = vid)
        else:
            return

        if del_only is not None:
            if del_only == ACL_IN:
                msgs = [self.del_flow(dp, self.acl_inT_id, match_in)]
            elif del_only == ACL_OUT:
                msgs = [self.del_flow(dp, self.acl_outT_id, match_out)]
        else:
            msgs = [self.del_flow(dp, self.acl_inT_id, match_in)]
            msgs += [self.del_flow(dp, self.acl_outT_id, match_out)]
        msgs += util.barrier_request(dp)
        util.send_msgs(dp, msgs)


    def make_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        return util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)

    def del_flow(self, dp, table_id = None, match = None, out_port=None, out_group=None):
        return util.del_flow(dp, self.cookie, table_id, match, out_port, out_group)

    def send_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        # используется если сообщение ловится одним dp, а слать нужно на другой dp
        m = util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)
        datapath.send_msg(m)


    def clean_all_flows(self, dp):
        "Remove all flows with the Simple switch cookie from all tables"
        msgs = []
        for t in self.tables.tables:
            i = self.tables.table_id(t.name)
            msgs += [self.del_flow (dp, i)]
            msgs += util.barrier_request(dp)
        return msgs