# -*- coding: utf-8 -*-

import yaml
import pathlib
from copy import deepcopy, copy
import helper_methods as util
from datapath import DatapathConfig
import ofp_custom_events as c_events
from vlan import Vlan
from acl import Acl
from collections import defaultdict

CONFIG_PATH = 'conf.yaml'


class Config:

    def __init__(self, config_path = CONFIG_PATH):
        self.mod_time = None
        self.dps = {} #dps that were found in config {dp_id: DatapathConfig}
        self.glob_settings = {} #TODO а они вообще хоть где-то используются?? если нет, убрать
        self.old_dps = None
        self.old_glob_settings = None
        self.active_dps = {} #dps from config, that are connected to controller
        self.inactive_dps = {} #dps from config, that become disconnected from controller
        #dp_id ожидающие воей очереди на подключения, когда такой dp появится в конфигурации, он будет зарегестрирован через процесс BackgroundApp
        self.waiting_to_connect_dps = []
        # self.ports_info_for_dp = {} #dp_id:its ports info

        self.vlans = {} #{name:Vlan}
        # forr vlan routing
        self.route_vlans = defaultdict(list) # { id: [vl_name] }
        self.acls = {} #{name:Acl}

        self.parse_config(config_path)

    def state(self):
        print()
        print('State of Config class')
        print('Self = ', id(self))
        print('old_dps = ', id(self.old_dps) )
        print('dps = ', id(self.dps) )
        print('waiting_to_connect_dps = ', id(self.waiting_to_connect_dps) )
        print()
        print('Self.dps')
        for d, a in self.dps.items():
            print (d, a)
        print()
        print('Self.old_dps')
        try:
            for d, a in self.old_dps.items():
                print (d, a)
        except Exception as e:
            print(e)
        print()
        print('waiting_to_connect_dps = ')
        for d in self.waiting_to_connect_dps:
            print(d)
        print('Self.route_vlans')
        print(self.route_vlans)
        print('End of state')


    def parse_config(self, config_path = CONFIG_PATH):
        if config_path is None:
            return
        with open(str(config_path)) as f:
            config = yaml.safe_load(f)
        fname = pathlib.Path(config_path)
        self.mod_time = fname.stat().st_mtime

        #delete old config
        try:
            self.dps.clear()
            self.glob_settings.clear()
            self.vlans.clear()
            self.acls.clear()
        except TypeError as e:
            print('Error while parse config ', e)

        vls = config.get('vlans')
        if vls is not None:
            for vname, settings in vls.items():
                self.vlans[vname] = Vlan(vname, settings)

        acls = config.get('acl')
        if acls is not None:
            for acl_name, settings in acls.items():
                self.acls[acl_name] = Acl(acl_name, settings)

        dps = config.get('dps')
        if dps is None:
            return
        for sw, settings in dps.items():
            i = settings.get('dp_id') 
            if i is not None:
                self.dps[i] = DatapathConfig(i, sw, settings)

        vl_routing = config.get('vlan-routing')
        if vl_routing is not None:
            self.route_vlans = vl_routing
        # print('!Self-route-Vlan = ', self.route_vlans)


    def conf_review(self):
        """validate config 
        проверка соответствия конфиги необходимому шаблону
        если конфига неправильная, то она заменяется old_config конфигой"""
        #VLAN APP
        for v in self.dps.values():
            for vp in v.ports.values():
                if vp.tagged_vlans is None and vp.native_vlan is None:
                    print('Error: dp %s port %s dont have vlan' % (v,vp))
                    return False
                #проверка того, что указанный на порту влан, присутствует в Config.vlans
                elif vp.tagged_vlans is not None and vp.native_vlan is not None:
                    print('Error: port %s is access and trunk at the same time' % vp)
                    return False
                elif vp.native_vlan is not None and vp.native_vlan not in self.vlans.keys():
                    print('Error: no such vlan %s on port %s' % vp.native_vlan, vp)
                    return False
                elif vp.tagged_vlans is not None:
                    for vl in vp.tagged_vlans:
                        if vl not in self.vlans.keys():
                            print('Error: no such vlan %s on port %s' % vl, vp)
                            return False 
                # аналогичная провекра для acl
                elif vp.acl_in is not None:
                    for ac in vp.acl_in:
                        if ac not in self.acls.keys():
                            print('Error: no such acl %s on port %s' % ac, vp)
                            return False 
                elif vp.acl_out is not None:
                    for ac in vp.acl_out:
                        if ac not in self.acls.keys():
                            print('Error: no such acl %s on port %s' % ac, vp)
                            return False 
        #L3 App
            if (len(v.announced_gws)>0 or len(v.other_gws)>0) and v.ospf_out is None:
                print('Error on dp with id %s: ip_gateways should be only on border router' % v.id)
                return False
        # Inter-vlan routing
            for vl_route in self.route_vlans.values():
                for vl in vl_route:
                    if vl not in self.vlans.keys():
                        print('Error: vlan %s is not announced'%vl)
                        return False
        return True


    def check_config_for_difference(self, new_config_path = CONFIG_PATH):
        """check if config was changed
        if previous config and certain config are different - return tuple of defferent objects"""
        if new_config_path is None:
            print('Error. Config_path is None')
            return False, {}

        #проверка того, редактировался ли файл конфигурации
        #TODO или один из вложенных в него файлов
        fname = pathlib.Path(new_config_path)
        if self.mod_time >= fname.stat().st_mtime:
            return False, {}

        #create deep copy
        self.old_dps = copy(self.dps)
        self.old_glob_settings = deepcopy(self.glob_settings)
        self.old_vlans = deepcopy(self.vlans) #используется в bakground. TODO создать в конфиге метод сохранения ее состояния, и в background уже вызывать его, вместо того, чтобы напрямую обращаться к данным 
        self.old_route_vlans = deepcopy(self.route_vlans)
        self.old_acls = deepcopy(self.acls)
        #changing the config
        self.parse_config(new_config_path)
        
        #Вариант два - кастомное сравнение конфиг
        #time to compare
        #1 find keys, that arent in both dictionaries
        old_keys = self.old_dps.keys()
        keys = self.dps.keys()
        in_both = old_keys & keys
        only_in_one = old_keys ^ keys
        
        #list of dps that are different and should be changed
        dps_tobe_changed = []
        #list of dps that exists only in old or in new dps
        only_old_dps = []
        only_new_dps = []
        #2 compare elements which keys are in both configs
        for dp_id in in_both:
            #if DP objects are different - remember them
            if not (self.dps[dp_id] == self.old_dps[dp_id]):
                dps_tobe_changed += [self.dps[dp_id]]
        #3 remember DatapathConfigs that exists only in one config
        for dp_id in only_in_one:
            dp = self.dps.get(dp_id)
            if dp is None:
                #DP is in the old one
                only_old_dps += [self.old_dps.get(dp_id)]
            else:
                #DP is in the new one
                only_new_dps += [dp]
        
        #dictionary with lists of chages
        changes = {}
        #remember, where we need changes
        if bool(dps_tobe_changed):
            changes['dps_tobe_changed'] = dps_tobe_changed
        if bool(only_old_dps):
            changes['only_old_dps'] = only_old_dps
        if bool(only_new_dps):
            changes['only_new_dps'] = only_new_dps
        
        #check glob settings dictionaries
        old_keys = self.old_glob_settings.keys()
        keys = self.glob_settings.keys()
        in_both = old_keys & keys
        only_in_one = old_keys ^ keys

        settings_changes = {}
        old_settings = {}
        new_settings = {}
        for settings in in_both:
            if self.old_glob_settings[settings]  != self.glob_settings[settings]:
                #if the same settings differs - we need to change themx
                settings_changes[settings] = self.glob_settings[settings]

        for settings in only_in_one:
            setting = self.glob_settings.get(settings)
            if setting is None:
                #setting is in the old one
                old_settings[settings] = self.old_glob_settings.get(settings)
            else:
                #setting is in the new one
                new_settings[settings] = setting
        if bool(settings_changes):
            changes['settings_changes'] = settings_changes
        if bool(old_settings):
            changes['old_settings'] = old_settings
        if bool(new_settings):
            changes['new_settings'] = new_settings

        # check vlan routing changes
        old_keys = self.old_route_vlans.keys()
        keys = self.route_vlans.keys()
        in_both = old_keys & keys
        only_in_one = old_keys ^ keys

        for r_id in in_both:
            if set(self.old_route_vlans[r_id]) != set(self.route_vlans[r_id]):
                changes['vlan_routing_changes'].append( [r_id, self.route_vlans[r_id], self.old_route_vlans[r_id]] )

        for r_id in only_in_one:
            vl_route = self.route_vlans.get(r_id)
            if vl_route is None:
                #setting is in the old one
                changes['vlan_routing_old'].append( [r_id, self.old_glob_settings.get(r_id)] )
            else:
                #setting is in the new one
                changes['vlan_routing_new'].append( [r_id, vl_route] )

        # check ACL changes
        old_keys = self.old_acls.keys()
        keys = self.acls.keys()
        in_both = old_keys & keys
        only_in_one = old_keys ^ keys

        for acl_id in in_both:
            if self.acls[acl_id] != self.acls[acl_id]:
                changes['acl_changes'].append( [acl_id, self.acls[acl_id], self.old_acls[acl_id]] )

        for acl_id in only_in_one:
            acl_list = self.acls.get(acl_id)
            if acl_list is None:
                #setting is in the old one
                changes['acl_old'].append( [acl_id, self.old_acls.get(acl_id)] )
            else:
                #setting is in the new one
                changes['acl_new'].append( [acl_id, acl_list] )

        # check vlan changes
        old_keys = self.old_vlans.keys()
        keys = self.vlans.keys()
        in_both = old_keys & keys
        only_in_one = old_keys ^ keys

        for vlan_name in in_both:
            if self.vlans[vlan_name] != self.old_vlans[vlan_name]:
                changes['vlan_changes'].append( [vlan_name, self.vlans[vlan_name], self.old_vlans[vlan_name]] )

        for vlan_name in only_in_one:
            vl = self.vlans.get(vlan_name)
            if vl is None:
                #setting is in the old one
                changes['vlan_old'].append( [vlan_name, self.old_vlans.get(vlan_name)] )
            else:
                #setting is in the new one
                changes['vlan_new'].append( [vlan_name, vl] )

        #return True if there are any changes
        return bool(changes), changes
    

    def register_dp(self, dp, port_params_msg = None):
        """проверяет, указан ли свитч с таким dp-id в конфиге
        TODO еще чтобы шла проверка по ключу или сертифкату перед присоединением свитча
        и если да, то сгенерирует ивенты для его настройки"""
        if dp.id in self.dps.keys():
            if dp.id in self.active_dps.keys():
                if port_params_msg is not None:
                    #узнаем, какие у dp есть порты
                    self.active_dps[dp.id].set_port_info(port_params_msg)
                return []
            #проверка, был ли свитч в отключенных
            if self.is_inactive(dp.id):
                #delete from inactive switches
                del self.inactive_dps[dp.id]
            if dp.id in self.waiting_to_connect_dps:
                self.waiting_to_connect_dps.remove(dp.id)
            #находим нужный DatapathConfig объект
            new_dp = self.dps[dp.id]
            new_dp.dp_obj = dp
            events = []
            events += [c_events.NewDp(dp)]
            #events += [all ports down]
            # print('@@ dp was configured ')
            # print(new_dp)
            events += new_dp.configure(ports_info = port_params_msg)
            #register as active
            self.active_dps[dp.id] = self.dps[dp.id]
            return events
        self.waiting_to_connect_dps += [dp.id]
        # print('DP was not nastroen')
        return []


    def unregister_dp(self, dp):
        """удаляет dp из активных и переносит его в неактивные
        если dp не был зарегистрирован, ничего не делает"""
        dp_val = self.active_dps.pop(dp.id, None)
        events = []
        if dp_val is not None:
            events += [c_events.LostDp(self.dps[dp.id])]
            self.inactive_dps[dp.id] = dp_val
            # if self.ports_info_for_dp.get(dp.id) is not None:
            #     del self.ports_info_for_dp[dp.id]
            print(dp_val.id, ' was unregistered')
            print('!Active dps = ', self.active_dps.keys())
            print('!All dps = ', self.dps.keys())
        else:
            print('DatapathConfig %s was not found in active_dps' % dp.id)
        return events


    def is_registered(self, dp_id):
        return dp_id in self.active_dps.keys()
    
    def is_inactive(self, dp_id):
        return dp_id in self.inactive_dps.keys()

        
    def all_gws(self):
        #возвращает все шлюзы по умолчанию
        gws = []
        for dp in self.active_dps.values():
            if dp.ospf_out is not None:
                for gw in dp.announced_gws:
                    gws+=[gw]
                for gw in dp.other_gws:
                    gws+=[gw]
        return gws

    def is_gwip(self, ip):
        #проверят, является ли ip шлюзом по умолчанию
        for dp in self.active_dps.values():
            if dp.ospf_out is not None:
                if ip in dp.announced_gws or ip in dp.other_gws:
                    return True
        return False