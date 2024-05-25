# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
#from ryu.lib.ofp_pktinfilter import packet_in_filter, RequiredTypeFilter
#from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4
from ryu.ofproto import ofproto_v1_3 as ofproto
from ryu.ofproto import ofproto_v1_3_parser as parser

import helper_methods as util
import table
import ofp_custom_events as c_ev
from config import Config

PRIORITY_MIN = 0
PRIORITY_DEF = 16000
PRIORITY_MAX = 32000
HARD_TIME = 40000
IDLE_TIME = 20000


class VlanApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto.OFP_VERSION]

    _CONTEXTS = {
        'tables': table.Tables,
        'net-config': Config
        }

    _EVENTS = [ c_ev.giveVlantoPorts ]

    def __init__(self, *args, **kwargs):
        super(VlanApp, self).__init__(*args, **kwargs)
        self.tables = kwargs['tables']
        self.net_config = kwargs['net-config']
        self.cookie = 20
        self.vl_in_table, self.vl_inT_id = self.tables.get_table('vl_in')
        self.vl_out_table, self.vl_outT_id = self.tables.get_table('vl_out')
        self.flood_table, self.floodT_id = self.tables.get_table('flood')
        self.vl_change_table, self.vl_changeT_id = self.tables.get_table('vl_change')
        self.vlan_to_ports = {} # {dp:{vid:[port.num]}}


    @set_ev_cls(c_ev.NewDp, MAIN_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.dp
        #add rules defined by config
        msgs = self.clean_all_flows(dp)
        msgs += self.add_default_flows(dp)
        util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.getVlantoPorts, MAIN_DISPATCHER)
    def _vl_to_ports_handler(self, ev):
        if ev.code == 4:
            vid = ev.dp
            self.send_event_to_observers(c_ev.giveVlantoPorts(vid, self.vlan_to_ports, code = ev.code, data = ev.data))
        else:
            dp = ev.dp
            self.send_event_to_observers(c_ev.giveVlantoPorts(dp, self.vlan_to_ports.get(dp.id), code = ev.code, data = ev.data))
        

    @set_ev_cls(c_ev.NewPortNativeVlan, MAIN_DISPATCHER)
    @set_ev_cls(c_ev.NewPortTaggedVlan, MAIN_DISPATCHER)
    def change_vl_handler(self, ev):
        p = ev.port
        dp = ev.dp.dp_obj
        #find port and all vlans it was in
        old_pvlans = []
        for vl in self.vlan_to_ports[dp.id]:
            for p_num in self.vlan_to_ports[dp.id][vl]:
                if p_num == p.num:
                    old_pvlans+=[vl]
                    break
                
        #delete flows with port 
        msgs = [self.del_flow(dp, self.vl_inT_id, match = parser.OFPMatch(in_port=p.num))]
        msgs += [self.del_flow(dp, self.vl_outT_id, match = parser.OFPMatch(metadata=p.num))]
        #delete broadcast rules port was in
        for vl in old_pvlans:
            msgs += self.del_broadcast_multicast_rules(dp, vl)
            #delete port from vlan_to_port from all vlans it was in
            self.vlan_to_ports[dp.id][vl].remove(p.num)
        msgs += util.barrier_request(dp)
        
        #add port in new vlans in vla to port
        #and add new flows with port
        new_pvlans = [] #and remember vlans port is in now
        if p.native_vlan is not None:
            #add native vlan flows
            vlan_num = self.net_config.vlans[p.native_vlan].vid
            msgs += self.add_native_vl_flows(dp, p.num, vlan_num)
            self.vlan_to_ports[dp.id][vlan_num] += [p.num]
            new_pvlans = [vlan_num]
        elif p.tagged_vlans is not None:
            #add tagged flows
            for vl in p.tagged_vlans:
                vlan_num = self.net_config.vlans[vl].vid
                msgs += self.add_tagged_vl_flows(dp, p.num, vlan_num)
                self.vlan_to_ports[dp.id][vlan_num] += [p.num]
                new_pvlans += [vlan_num]

        #add deleted broadcast rules without port
        for vl in old_pvlans:
            if vl not in new_pvlans:
                msgs += self.add_broadcast_multicast_flows(dp, vl)
        #add deleted and new broadcast rules with port
        for vl in new_pvlans:
            msgs += self.add_broadcast_multicast_flows(dp, vl)

        util.send_msgs(dp, msgs)

    
    @set_ev_cls(c_ev.PortStateChanged, MAIN_DISPATCHER)
    def _change_port_state_handler(self, ev):
        port = ev.port
        state = port.state
        
        if state == 2 or state == 1:
            # удалить все бродкасть правила, связанные с этим портом и заинсталлить новые - без него (или с ним, если его состояние - не два)
            dp = self.net_config.dps[ev.dp_num]
            dp = dp.dp_obj
            #find port and all vlans it was in
            vlans = []
            for vl in self.vlan_to_ports[dp.id]:
                for p_num in self.vlan_to_ports[dp.id][vl]:
                    if p_num == port.num:
                        vlans+=[vl]
                        break
            msgs = []
            # print('!PortStateChanged messages for dp =%s port %s'%(dp.id, port.num))
            # print('vlans = ', vlans)
            if len(vlans) < 1:
                return

            #delete broadcast rules port was in
            for vl in vlans:
                msgs += self.del_broadcast_multicast_rules(dp, vl)
                #delete port from vlan_to_port from all vlans it was in
                # self.vlan_to_ports[dp.id][vl].remove(port.num)
            msgs += util.barrier_request(dp)
            
            #add deleted broadcast rules without port
            for vl in vlans:
                msgs += self.add_broadcast_multicast_flows(dp, vl)

            util.send_msgs(dp, msgs)


    @set_ev_cls(c_ev.DelBorderRouter, MAIN_DISPATCHER)
    @set_ev_cls(c_ev.NewBorderRouter, MAIN_DISPATCHER)
    def _border_state_changed_handler(self, ev):
        old_dpc = ev.old_dp_conf
        new_dpc = ev.dp_conf
        msgs = []
        # установить влан на прошлый порт, удалить влан с настоящего бордер свитча
        if old_dpc is not None and old_dpc.ospf_out is not None:
            # удалить потоки для порта
            msgs+=self.del_ospf_flows(new_dpc.dp_obj, new_dpc.ospf_out )
        if new_dpc.ospf_out is not None:
            # установить особые потоки влан на порт
             msgs+=self.add_ospf_flows(new_dpc.dp_obj, new_dpc.ospf_out )

        util.send_msgs(new_dpc.dp_obj, msgs)


    @set_ev_cls(c_ev.VlanChanged, MAIN_DISPATCHER)
    def _vl_route_changed_handler(self, ev):
        # при изменении влана - смотрим, изменились ли сетки, если да. Перестраиваем потоки влана ospf_out портов на всех свитчах
        # обрабатывает только изменения ospf порта
        # TODO TEST
        new_vl = ev.new_vlan
        old_vl = ev.old_vlan
        print('!!! VlanChanged vlans = ', old_vl, new_vl)
        for dp_conf in self.net_config.dps.values():
            if dp_conf.ospf_out is not None:
                msgs = []
                if old_vl.net is not None:
                    # очистить потоки для влан
                    msgs = self.del_ospf_flows_for_vl(dp_conf.dp_obj, dp_conf.ospf_out, old_vl)
                if new_vl.net is not None:
                    # установить потоки для влан
                    msgs += self.add_ospf_flows_for_vl(dp_conf.dp_obj, dp_conf.ospf_out, new_vl)
            util.send_msgs(dp_conf.dp_obj, msgs)


    @set_ev_cls(c_ev.DelVlan, MAIN_DISPATCHER)
    def _vl_route_changed_handler(self, ev):
        # удаляем потоки влана со всех dp
        # используется только для удаления in потоков ospf_out порта
        # TODO TEST
        vl = ev.vlan
        if vl.net is not None:
            for dp_conf in self.net_config.dps.values():
                if dp_conf.ospf_out is not None:
                    msgs = self.del_ospf_flows_for_vl(dp_conf.dp_obj, dp_conf.ospf_out, vl)
                util.send_msgs(dp_conf.dp_obj, msgs)


    @set_ev_cls(c_ev.NewVlan, MAIN_DISPATCHER)
    def _vl_route_changed_handler(self, ev):
        # устанавливаем потоки влана на все dp
        # используется только для установки in потоков ospf_out порта, тк потоки на портах устанваливаются в других событиях
        # TODO TEST
        vl = ev.vlan
        if vl.net is not None:
            for dp_conf in self.net_config.dps.values():
                if dp_conf.ospf_out is not None:
                    msgs = self.add_ospf_flows_for_vl(dp_conf.dp_obj, dp_conf.ospf_out, vl)
                util.send_msgs(dp_conf.dp_obj, msgs)


    def del_broadcast_multicast_rules(self, dp, vl_num):
        # delete broadcast
        match = parser.OFPMatch(eth_dst='ff:ff:ff:ff:ff:ff', vlan_vid = 4096+vl_num) 
        msgs = [self.del_flow(dp, self.floodT_id, match)]
        #delete multicast
        flood_addrs = [
            ('01:80:c2:00:00:00', '01:80:c2:00:00:00'), # 802.x
            ('01:00:5e:00:00:00', 'ff:ff:ff:00:00:00'), # IPv4 multicast
            # ('33:33:00:00:00:00', 'ff:ff:00:00:00:00'), # IPv6 multicast
        ]
        for eth_dst in flood_addrs:
            match = parser.OFPMatch(eth_dst=eth_dst, vlan_vid = 4096+vl_num)
            msgs += [self.del_flow(dp, self.floodT_id, match)]
        return msgs


    def clean_all_flows(self, dp):
        "Remove all flows with the Simple switch cookie from all tables"
        msgs = []
        for t in self.tables.tables:
            i = self.tables.table_id(t.name)
            msgs += [self.del_flow (dp, i)]
            msgs += util.barrier_request(dp)
        return msgs


    def add_default_flows(self, dp):
        "Add the default flows needed for this environment"
        msgs = []
        sw = self.net_config.dps[dp.id]
        ospf_p = -1
        if sw.ospf_out is not None:
            ospf_p = sw.ospf_out

        vl_to_p = self.vlan_to_ports[dp.id] = {}
        for port in sw.ports.values():
            if port.num == ospf_p:
                # add special none vlan flows
                test = self.add_ospf_flows(dp, ospf_p)
                # print('Add ospf messages ', dp.id, ospf_p, test)
                msgs+=test
                continue

            if port.native_vlan is not None:
                vlan_num = self.net_config.vlans[port.native_vlan].vid
                msgs += self.add_native_vl_flows(dp, port.num, vlan_num)
                if vlan_num not in vl_to_p.keys():
                    vl_to_p[vlan_num] = [port.num]
                else:
                    vl_to_p[vlan_num] = vl_to_p[vlan_num]+[port.num]

            elif port.tagged_vlans is not None:
                for vl in port.tagged_vlans:
                    vlan_num = self.net_config.vlans[vl].vid
                    msgs += self.add_tagged_vl_flows(dp, port.num, vlan_num)
                    if vlan_num not in vl_to_p.keys():
                        vl_to_p[vlan_num] = [port.num]
                    else:
                        vl_to_p[vlan_num] = vl_to_p[vlan_num]+[port.num]
                    
        #broadcast rules
        #запоминаем порты в каких вланах, при бродкасте вылазим только по этим портам
        for vl in vl_to_p:
            msgs += self.add_broadcast_multicast_flows(dp, vl)

        # Drop rules
        ## VLAN IN TABLE
        match = parser.OFPMatch()
        msgs += [self.make_message (dp, self.vl_inT_id, PRIORITY_MIN, match)]

        ## VLAN OUT TABLE
        #переходить во flood table, if no match
        inst = self.tables.goto_next_of(self.vl_out_table)
        msgs += [self.make_message (dp, self.vl_outT_id, PRIORITY_MIN, parser.OFPMatch(), inst)]

        #FLOOD table
        #dont flood lldp
        #TODO ставить поток его в другой таблице, чтоб пакет меньше обрабатывался свитчом
        match = parser.OFPMatch(eth_dst='01:80:c2:00:00:0e', eth_type = 35020)
        msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MAX+1, match)]


        # Rules for changing vlan by metadata for broadcast traffic
        flood_addrs = [ ('01:80:c2:00:00:00', '01:80:c2:00:00:00'), ('01:00:5e:00:00:00', 'ff:ff:ff:00:00:00'), ('ff:ff:ff:ff:ff:ff')  ]
            
        inst = [parser.OFPInstructionWriteMetadata(metadata = 0, metadata_mask = 0xFFFFFFFF)]
        inst += self.tables.goto_next_of(self.vl_change_table)
        for vl_route in self.net_config.route_vlans.values():
            for vl_name in vl_route:
                vid = self.net_config.vlans[vl_name].vid
                # проверяем метадату смены айпи
                for eth_dst in flood_addrs:
                    match = parser.OFPMatch(metadata = vid*1000, eth_dst=eth_dst) #без eth_dst потоки не ставяться
                    actions = [parser.OFPActionSetField(vlan_vid = 4096+vid)]
                    msgs += [self.make_message (dp, self.vl_changeT_id, PRIORITY_DEF, match, inst, actions)]

        match = parser.OFPMatch()
        inst = self.tables.goto_next_of(self.vl_change_table)
        msgs += [self.make_message (dp, self.vl_changeT_id, PRIORITY_MIN, match, inst)]

        return msgs


    def add_ospf_flows(self, dp, port_num):
        msgs = []
        for vl in self.net_config.vlans.values():
            if vl.net is not None:
                msgs += self.add_ospf_flows_for_vl(dp, port_num, vl)
        
        ## VLAN OUT TABLE
        # снимаем метку влана с влан пакетов
        match = parser.OFPMatch(metadata=port_num, vlan_vid=(0x1000, 0x1000) )
        actions = [parser.OFPActionPopVlan()]
        actions += [parser.OFPActionOutput(port_num)]
        msgs +=  [self.make_message (dp, self.vl_outT_id, PRIORITY_DEF, match, actions=actions)]

        # остальные пакеты шлем так
        match = parser.OFPMatch(metadata=port_num)
        actions = [parser.OFPActionOutput(port_num)]
        msgs += [self.make_message (dp, self.vl_outT_id, PRIORITY_DEF-1, match, actions=actions)]
        return msgs

    
    def add_ospf_flows_for_vl(self, dp, port_num, vl):
        ## VLAN IN TABLE
        # надеваем метку влана в зависимости от сети назначения
        msgs = []
        for net in vl.net:
            # TODO добавить vlanid чтобы не вешать метку влана два раза?
            match = parser.OFPMatch(in_port=port_num, eth_type = 0x800, ipv4_src = net)
            actions = [parser.OFPActionPushVlan()]
            actions += [parser.OFPActionSetField(vlan_vid = 4096+vl.vid)]
            # также для маршрутизации между вланами - красим метадатой
            inst = [parser.OFPInstructionWriteMetadata(metadata = vl.vid*1000, metadata_mask = 0xFFFFFFFF)]
            inst += self.tables.goto_next_of(self.vl_in_table)
            msgs += [self.make_message (dp, self.vl_inT_id, PRIORITY_DEF, match, inst, actions)]
        
            # тк ip_src может быть не из сети банка, то ставим потоки с меньшим приоритетом, которые будут красить такой источник вланом назначения 
            match = parser.OFPMatch(in_port=port_num, eth_type = 0x800, ipv4_dst = net)
            msgs += [self.make_message (dp, self.vl_inT_id, PRIORITY_DEF - 2, match, inst, actions)]
        return msgs


    def del_ospf_flows(self, dp, port_num):
        msgs = []
        for vl in self.net_config.vlans.values():
            if vl.net is not None:
                msgs += self.del_ospf_flows_for_vl(dp, port_num, vl)
        return msgs


    def del_ospf_flows_for_vl(self, dp, port_num, vl):
        ## VLAN IN TABLE
        # надеваем метку влана в зависимости от сети назначения
        msgs = []
        for net in vl.net:
            match = parser.OFPMatch(in_port=port_num, eth_type = 0x800, ipv4_dst = net)
            msgs += [self.del_flow(dp, self.vl_outT_id, match)]
        if bool(msgs):
            msgs += util.barrier_request(dp)
        return msgs


    def add_native_vl_flows(self, dp, port_num, vl_num):
        ## VLAN IN TABLE
        match = parser.OFPMatch(in_port=port_num, vlan_vid=0)
        actions = [parser.OFPActionPushVlan()]
        actions += [parser.OFPActionSetField(vlan_vid = 4096+vl_num)]
        # для маршрутизации между вланами - красим метадатой
        inst = [parser.OFPInstructionWriteMetadata(metadata = vl_num*1000, metadata_mask = 0xFFFFFFFF)]
        inst += self.tables.goto_next_of(self.vl_in_table)
        msgs = [self.make_message (dp, self.vl_inT_id, PRIORITY_DEF, match, inst, actions)]

        ## VLAN OUT TABLE
        match = parser.OFPMatch(metadata=port_num, vlan_vid=4096+vl_num)
        actions = [parser.OFPActionPopVlan()]
        actions += [parser.OFPActionOutput(port_num)]
        msgs += [self.make_message (dp, self.vl_outT_id, PRIORITY_DEF, match, actions=actions)]
        return msgs


    def add_tagged_vl_flows(self, dp, port_num, vl_num):
        ## VLAN IN TABLE
        match = parser.OFPMatch(in_port=port_num, vlan_vid=4096+vl_num)
        # для маршрутизации между вланами - красим метадатой
        inst = [parser.OFPInstructionWriteMetadata(metadata = vl_num*1000, metadata_mask = 0xFFFFFFFF)]
        inst += self.tables.goto_next_of(self.vl_in_table)
        msgs = [self.make_message (dp, self.vl_inT_id, PRIORITY_DEF, match, inst)]

        ## VLAN OUT TABLE
        match = parser.OFPMatch(metadata=port_num, vlan_vid=4096+vl_num)
        actions = [parser.OFPActionOutput(port_num)]
        msgs += [self.make_message (dp, self.vl_outT_id, PRIORITY_DEF, match, actions=actions)]
        return msgs


    def add_broadcast_multicast_flows(self, dp, vlan_num):
        msgs = []
        t_ports = []
        n_ports = []
        if len(self.vlan_to_ports[dp.id][vlan_num]) == 0:
            return []
        for p_num in self.vlan_to_ports[dp.id][vlan_num]:
            #find tagged ports
            p = self.net_config.dps[dp.id].ports[p_num]
            if p.state != 2:
                if p.tagged_vlans is not None:
                    t_ports += [p.num]
                else:
                    n_ports += [p.num]
            # else:
            #     print('#######  Port state = 2 dp=%s port=%s'%(dp.id, p.num))

        for p_num in self.vlan_to_ports[dp.id][vlan_num]:
            match = parser.OFPMatch(eth_dst='ff:ff:ff:ff:ff:ff', vlan_vid = 4096+vlan_num, in_port = p_num)
            actions = []
            for p in t_ports:
                if p != p_num:
                #for all tagged ports dont need to pop vlan
                    actions += [parser.OFPActionOutput(p)]
            #for access ports need
            actions += [parser.OFPActionPopVlan()]
            for p in n_ports:
                if p != p_num:
                    actions += [parser.OFPActionOutput(p)]
            msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MAX, match, actions = actions)]


            # TODO delete this
            # match = parser.OFPMatch(eth_dst='00:00:00:00:00:00', vlan_vid = 4096+vlan_num, in_port = p_num)
            # msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MAX, match, actions = actions)]
            # TODO delete

            #add multicast match - Flood multicast (Mimic Faucet)
            flood_addrs = [
                ('01:80:c2:00:00:00', '01:80:c2:00:00:00'), # 802.x
                ('01:00:5e:00:00:00', 'ff:ff:ff:00:00:00'), # IPv4 multicast
                # ('33:33:00:00:00:00', 'ff:ff:00:00:00:00'), # IPv6 multicast
            ]
            for eth_dst in flood_addrs:
                match = parser.OFPMatch(eth_dst=eth_dst, vlan_vid = 4096+vlan_num, in_port = p_num)
                msgs += [self.make_message (dp, self.floodT_id, PRIORITY_MAX, match, actions = actions)]
        return msgs
        


    def make_message (self, datapath, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
        return util.make_message (datapath, self.cookie, table_id, priority, match, instructions, actions, buffer_id, command, idle_timeout, hard_timeout)

    def del_flow(self, dp, table_id = None, match = None, out_port=None, out_group=None):
        return util.del_flow(dp, self.cookie, table_id, match, out_port, out_group)

    def show_vlan_to_ports(self):
        for ddp in self.vlan_to_ports:
            print('!!show_vlan_to_ports\n dp  ', ddp)
            for vid in self.vlan_to_ports[ddp]:
                print('@@ vid  ', vid)
                print('Ports:')
                for pp in self.vlan_to_ports[ddp][vid]:
                    print(pp)
        print()
        print()
