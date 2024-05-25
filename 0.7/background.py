# -*- coding: utf-8 -*-

from ryu.base import app_manager
from ryu.ofproto import ofproto_v1_3
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.controller import ofp_event

import time

from config import Config
import ofp_custom_events as c_ev
from copy import deepcopy, copy


class BackgroundApp(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    
    _CONTEXTS = {
        'net-config': Config
        }

    _EVENTS = [c_ev.NewDp, c_ev.LostDp,
        c_ev.NewPortNativeVlan, c_ev.NewPortTaggedVlan,
        c_ev.PortUp, c_ev.PortDown,
        c_ev.NewAnnouncedGw, c_ev.NewGw, c_ev.DelAnnouncedGw, c_ev.DelGw,
        c_ev.NewBorderRouter, c_ev.DelBorderRouter,
        c_ev.PortStateChanged,
        c_ev.AclInChanged, c_ev.AclOutChanged, c_ev.AclChanged, c_ev.DelAcl, c_ev.NewAcl,
        c_ev.VlanChanged]

    def __init__(self, *args, **kwargs):
        super(BackgroundApp, self).__init__(*args, **kwargs)
        self.interval = 60
        self.net_config = kwargs['net-config']
        

    @set_ev_cls(c_ev.StartBackground, CONFIG_DISPATCHER)
    def new_switch_handler(self, ev):
        self.run() 


    def run(self):
        """ Method that runs forever """
        while True:
            time.sleep(self.interval)
            res, changes = self.net_config.check_config_for_difference() #'myK/conf_old.yaml'
            if self.net_config.conf_review():
                if res:
                    evs = []
                    #создать changes
                    for key,val in changes.items():
                        if key == 'dps_tobe_changed':
                            # print('!Datapath need change')
                            # self.net_config.state()
                            # print()
                            # print()
                            for dp in val:
                                # print('work with val  ', dp, '\n old dp = ', self.net_config.old_dps[dp.id])
                                evs += dp.configure(self.net_config.old_dps[dp.id])
                            # print()
                            # print()
                            # print('!Datapaths changed')
                            # self.net_config.state()
                        if key == 'only_new_dps':
                            #MainApp при присоединении свитча, пишет его айди в очередь на регистрацию, если для свитча еще нет конфиги
                            # здесь уже проверяется, есть ли указанный в конфиге новый свитч в очереди на присоединение
                            # если есть - регистрируем его, иначе ничего не делаем, тк при присоединении такого свитча MainApp его зарегает 
                            for dp in val:
                                if dp.id in self.net_config.waiting_to_connect_dps:
                                    self.net_config.register_dp(dp)
                        elif key == 'only_old_dps':
                            for dp in val:
                                self.net_config.unregister_dp(dp)
                        elif key == 'vlan_routing_changes':
                            for route_change in changes[key]:
                                evs += [c_ev.VlRouteChange(r_id = route_change[0], new_route = route_change[1], old_route = route_change[2])]
                        elif key == 'vlan_routing_old':
                            for route_change in changes[key]:
                                evs += [c_ev.VlRouteDelete(r_id = route_change[0], route = route_change[1])]
                        elif key == 'vlan_routing_new':
                            for route_change in changes[key]:
                                evs += [c_ev.VlRouteNew(r_id = route_change[0], route = route_change[1])]

                        elif key == 'acl_changes':
                            for acl_change in changes[key]:
                                evs += [c_ev.AclChanged(acl_change[0], acl_change[1], acl_change[2])]
                        elif key == 'acl_old':
                            for acl_change in changes[key]:
                                evs += [c_ev.DelAcl(acl_change[0], acl_change[1])]
                        elif key == 'acl_new':
                            for acl_change in changes[key]:
                                evs += [c_ev.NewAcl(acl_change[0], acl_change[1])]

                        elif key == 'vlan_changes':
                            for vlan_change in changes[key]:
                                evs += [c_ev.VlanChanged(vlan_change[0], vlan_change[1], vlan_change[2])]
                        # это вроде бы не нужно, тк конфиг_ревью не пропустит порты с вланами, которые не указаны во vlans, а при изменении vlans на портах, порты сами кинут событие об изменении вланов - теперь нужно, тк у вланов могут быть сетки
                        elif key == 'vlan_old':
                            for vlan_change in changes[key]:
                                evs += [c_ev.DelVlan(vlan_change[0], vlan_change[1])]
                        elif key == 'vlan_new':
                            for vlan_change in changes[key]:
                                evs += [c_ev.NewVlan(vlan_change[0], vlan_change[1])]

                        elif key == 'settings_changes':
                            print('settings_changes - doing nothing')
                        elif key == 'old_settings':
                            print('old_settings - doing nothing')
                        elif key == 'new_settings':
                            print('new_settings - doing nothing')
                    #применить changes
                    for ev in evs:
                        self.send_event_to_observers(ev)
            else:
                #если конфига не правильная вернуть ее старую копию
                self.net_config.dps = copy(self.net_config.old_dps)
                self.net_config.glob_settings = deepcopy(self.net_config.old_glob_settings)
                self.net_config.vlans = deepcopy(self.net_config.old_vlans)
                self.net_config.route_vlans = deepcopy(self.net_config.old_route_vlans)
                self.net_config.acls = deepcopy(self.net_config.old_acls)

