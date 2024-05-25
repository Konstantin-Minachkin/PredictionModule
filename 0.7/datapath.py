# -*- coding: utf-8 -*-

from helper_methods import props, barrier_request
from ryu.ofproto import ofproto_v1_3_parser as parser
import ofp_custom_events as c_ev
from port import Port
import ipaddress
import helper_methods as util
import random

class DatapathConfig:

    def __init__(self, i, name, settings):
        self.id = i
        self.dp_obj = None
        self.name = name
        self.native_vlan = settings.get('native_vlan')
        self.ports = self.parse_ports(settings.get('ports')) # dictionary (port_num:Port)
        ##L3
        self.ospf_out = settings.get('border_ospf') #out_ospf port
        self.announced_gws = {} #{mac:ipaddress.ip_interface}
        self.other_gws = {} #{mac:ipaddress.ip_interface}
        ip_gws = settings.get('ip_gateways')
        if ip_gws is not None:
            if ip_gws.get('announce') is not None:
                for gw in ip_gws.get('announce'):
                    self.announced_gws[self.generate_mac()] = ipaddress.ip_interface(gw)
            if ip_gws.get('others') is not None:
                for gw in ip_gws.get('others'):
                    self.other_gws[self.generate_mac()] = ipaddress.ip_interface(gw)


    def __str__(self):
        args = []
        args.append('<DatapathConfig')
        for prop in props(self):
            if isinstance(getattr(self, prop), dict):
                #взять каждый элемент новых настроек
                for key in getattr(self, prop).keys():
                    args.append('\n%s = %s' % (prop, getattr(self, prop).get(key)))
            else:
                args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ' '.join(args)


    def __eq__(self, other):
        """funcion of eq of two DatapathConfigs. 
        Dont compare some DP settings (like description or name, for example)"""
        # assert(isinstance(other, DatapathConfig), 'DatapathConfig must eq only with DatapathConfig')
        for prop in props(self):
            #если характиристика - словарь
            if isinstance(prop, dict):
                #взять каждый элемент новых настроек
                for key in getattr(self, prop).keys():
                    old_p = getattr(other, prop).get(key)
                    new_p = getattr(self, prop).get(key)
                    #если порты не равны
                    if new_p != old_p or (new_p is not None and old_p is None) or (old_p is None and new_p is not None):
                        return False
            #если лист
            elif isinstance(getattr(self, prop), list):
                if set(getattr(self, prop)) != set(getattr(other, prop)):
                    return False
            #в ином случае просто сравнить значения
            elif getattr(self, prop) != getattr(other, prop):
                    return False
        return True

    def state(self):
        print()
        print('State of DatapathConfig class')
        print('Self = ', id(self))
        print('ports = ', id(self.ports) )
        print()
        print('Self.ports')
        for d, a in self.ports.items():
            print (d, a)


    def get_port(self, port_id):
        return self.ports.get(port_id)


    def parse_ports(self, ports_settings):
        ports = {}
        for num, settings in ports_settings.items():
            if settings is None:
                settings = {}
            #формируем настройки для передачи в port
            settings['dp_native_vlan'] = self.native_vlan
            ports[num] = Port(num, settings)
        return ports

    def set_port_info(self, info_msg):
        # есть еще такие поля (p., , p.name, p.config, p.state, p.peer, p.curr_speed, p.max_speed))
        ports_dict_del = {}
        for p in info_msg:
            if p.port_no in self.ports.keys():
                self.ports[p.port_no].mac = p.hw_addr
                self.ports[p.port_no].speed = p.curr_speed
            elif p.port_no <= 96: #чтобы не зацепить порт контроллера в ovs, так то в "реальности", порт, который будет идти от свитча к контроллеру, должен быть указан в конфиге
                ports_dict_del[p.port_no] = Port(p.port_no, settings = {'mac':p.hw_addr, 'speed':p.curr_speed})
        return ports_dict_del

    def configure(self, old_dp = None, ports_info = []):
        #old_dp - is DatapathConfig

        #словари словарей содержащие объекты, которые
        #есть в обоих
        temp_dict_ch = {}
        #есть только в новом
        temp_dict_add = {}
        #есть только в старом
        temp_dict_del = {}

        #{port_num: Port}
        ports_dict_add = {}
        ports_dict_del = {}
        ports_dict_ch = {}

        #for ip gateways
        an_ips_list_add = {}
        an_ips_list_del = {}
        oth_ips_list_add = {}
        oth_ips_list_del = {}

        #заполняем портов для датапафы
        ports_dict_del.update(self.set_port_info(ports_info))
                
        #dictionary of settings we need to change
        settings = {} 
        for prop in props(self):
            
            if old_dp is None:
            #настроить с нуля свитч на основе характеристик экзмепляра DP
                if isinstance(getattr(self, prop), dict):
                    prop_vals = list(getattr(self, prop).values())
                    #чтобы обрабатывать пустые словари
                    if len(prop_vals) > 0:
                        #чтобы смотреть тип словаря
                        first_el = prop_vals[0]
                        if isinstance(first_el, Port):
                            #если это словарь портов, то смотрим, какие необходимо включить, а какие выкл
                            for key in getattr(self, prop).keys():
                                new_p = getattr(self, prop).get(key)
                                if new_p.state == 1 or new_p.state == 2:
                                    ports_dict_add[key] = new_p
                                else:
                                    ports_dict_del[key] = new_p
                        #если это слворь шлюзов - добавляем их
                        elif isinstance(first_el, ipaddress.IPv4Interface):
                            if prop == 'announced_gws':
                                for key in getattr(self, prop).keys():
                                    an_ips_list_add[key] = getattr(self, prop).get(key)
                            else:
                                for key in getattr(self, prop).keys():
                                    oth_ips_list_add[key] = getattr(self, prop).get(key)
                        # если словарь пустой - ничего не делаем
                else:
                    settings[prop] = getattr(self, prop)

            else:
                #изменить настройки свитча, основываясь на его нынешних и прошлых настройках, указанных в old_dp
                #если характиристика - словарь
                if isinstance(getattr(self, prop), dict):
                    prop_vals = getattr(self, prop).values()
                    empty_dict = False
                    #чтобы обрабатывать пустые словари
                    if len(prop_vals) > 0:
                        first_el = list(prop_vals)[0]
                    else: #if len(prop_vals) == 0
                        old_prop_vals = getattr(old_dp, prop).values()
                        if len(old_prop_vals) > 0:
                            first_el = list(old_prop_vals)[0]
                        else:
                            #получается у нас два пустых словаря, ничего делать не надо
                            empty_dict = True

                    if not empty_dict:
                        if isinstance(first_el, ipaddress.IPv4Interface):
                            old_gws = getattr(old_dp, prop).values()
                            new_gws = prop_vals
                            in_one = list( set(new_gws) ^ set(old_gws) )
                            in_both = list( set(new_gws) & set(old_gws) )
                            # in_one = new_gws ^ old_gws
                            # in_both = new_gws & old_gws
                            #старые маки для тех же интерфейсов необходимо запоминать, новые убрать
                            for val in in_both:
                                #TODO здесь берем ключ проходя по словарю - сложность у алгоритма большая, тк там просто в цикле перебор, мб как-то по другому сделать??
                                old_mac = util.get_key(getattr(old_dp, prop), val)
                                new_mac = util.get_key(getattr(self, prop), val)
                                #сохраняем ранее сгенерированный МАС
                                if prop == 'announced_gws':
                                    print(self.announced_gws.items())
                                    print()
                                    print(old_dp.announced_gws.items())
                                    del self.announced_gws[new_mac]
                                    self.announced_gws[old_mac] = val 
                                else:
                                    del self.other_gws[new_mac]
                                    self.other_gws[old_mac] = val 
                            
                            for val in in_one:
                                if val in old_gws:
                                    old_mac = util.get_key(getattr(old_dp, prop), val)
                                    if prop == 'announced_gws':
                                        an_ips_list_del[old_mac] = val
                                    else:
                                        oth_ips_list_del[old_mac] = val   
                                else:
                                    new_mac = util.get_key(getattr(self, prop), val)
                                    if prop == 'announced_gws':
                                        an_ips_list_add[new_mac] = val
                                    else:
                                        oth_ips_list_add[new_mac] = val

                        else: #elif isinstance(first_el, Port):
                        #взять каждый элемент новых настроек
                            in_both = getattr(self, prop).keys() & getattr(old_dp, prop).keys()
                            in_one = getattr(self, prop).keys() ^ getattr(old_dp, prop).keys()
                            
                            for key in in_both:
                                old_p = getattr(old_dp, prop).get(key)
                                new_p = getattr(self, prop).get(key)
                                if new_p != old_p:
                                    ports_dict_ch[key] = old_p
                            
                            for key in in_one:
                                old_p = getattr(old_dp, prop).get(key)
                                new_p = getattr(self, prop).get(key)
                                if new_p is None:
                                    #если есть только в старом
                                    ports_dict_del[key] = old_p        
                                else:   # if old_p is None:
                                    #если только в новом
                                    ports_dict_add[key] = new_p

                elif getattr(self, prop) != getattr(old_dp, prop):
                    #need to change switch settings to the current state
                    if (prop == 'dp_obj') and getattr(old_dp, prop) is not None and getattr(self, prop) is None:
                        self.dp_obj = getattr(old_dp, prop)
                    else:
                        settings[prop] = getattr(self, prop)

        if bool(ports_dict_ch):
            temp_dict_ch['port'] = ports_dict_ch
        if bool(ports_dict_add):
            temp_dict_add['port'] = ports_dict_add
        if bool(ports_dict_del):
            temp_dict_del['port'] = ports_dict_del
        if bool(an_ips_list_add):
            temp_dict_add['an_gws'] = an_ips_list_add
        if bool(oth_ips_list_add):
            temp_dict_add['oth_gws'] = oth_ips_list_add
        if bool(an_ips_list_del):
            temp_dict_del['an_gws'] = an_ips_list_del
        if bool(oth_ips_list_del):
            temp_dict_del['oth_gws'] = oth_ips_list_del

        if bool(temp_dict_ch):
            settings['temp_dict_ch'] = temp_dict_ch
        if bool(temp_dict_add):
            settings['temp_dict_add'] = temp_dict_add
        if bool(temp_dict_del):
            settings['temp_dict_del'] = temp_dict_del
        return self.apply_settings(settings, old_dp)


    def apply_settings(self, settings, old_dp):
        """make OPFmessages, that apply settings to datapath
        settings - словарь характеристик которые надо установить {имя характеристики :значение}"""
        events = []
        # print('Apply setting = ', settings)
        try:
            for setting in settings.keys():
                #просмотреть все пришедшие настройки портов. Выполнить опредленные действия в зависимости от этого
                if setting == 'temp_dict_add':
                    #add new objects to switch
                    dict_add = settings[setting]
                    for add_setting in dict_add.keys():
                        if add_setting == 'port':
                            #add new ports
                            ports_add = dict_add[add_setting]
                            for port_id in ports_add.keys():
                                #добавляет список ивентов для первоначальной настройки порта
                                events += self.add_port(ports_add[port_id])
                        elif add_setting == 'an_gws':
                            gw_add = dict_add[add_setting]
                            for mac in gw_add.keys():
                                #шлем событие о новом шлюзе по умолчанию
                                events += [c_ev.NewAnnouncedGw(self, gw_add[mac], mac)]
                        elif add_setting == 'oth_gws':
                            gw_add = dict_add[add_setting]
                            for mac in gw_add.keys():
                                events += [c_ev.NewGw(self, gw_add[mac], mac)]

                elif setting == 'temp_dict_ch':
                    dict_ch = settings[setting]
                    for ch_setting in dict_ch.keys():
                        if ch_setting == 'port':
                            ports_ch = dict_ch[ch_setting]
                            for port_id in ports_ch.keys():
                                #добавляет список сообщений для конфигурации порта
                                events += self.get_port(port_id).configure(self, ports_ch[port_id])

                elif setting == 'temp_dict_del':
                    dict_del = settings[setting]
                    for del_setting in dict_del.keys():
                        if del_setting == 'port':
                            ports_del = dict_del[del_setting]
                            for port_id in dict_del[del_setting]:
                                events += self.del_port(ports_del[port_id])
                        elif del_setting == 'an_gws':
                            gw_del = dict_del[del_setting]
                            for mac in gw_del.keys():
                                #шлем событие об удалении ГВ
                                events += [c_ev.DelAnnouncedGw(self, gw_del[mac], mac)]
                                #удаляем из словаря
                                del self.announced_gws[mac]
                        elif del_setting == 'oth_gws':
                            gw_del = dict_del[del_setting]
                            for mac in gw_del.keys():
                                #шлем событие об удалении ГВ
                                events += [c_ev.DelGw(self, gw_del[mac], mac)]
                                #удаляем из словаря
                                del self.other_gws[mac]
            
            #обработка общих настроек свитча  
                elif setting == 'name':
                    print('!New name is ', settings[setting])
                    #как пример - генерация ивента для какой-то общий DP настройки, передается в таком случае весь DP объект
                    # events += [events.NewName(self)}
                elif setting == 'ospf_out':
                    try:
                        old_dp_val = getattr(old_dp, setting)
                    except AttributeError as e:
                        old_dp_val = None
                    if settings.get(setting) is None and old_dp_val is not None:
                        # ловит ситуацию, когда из конфигурации убирают border_ospf
                        events += [c_ev.DelBorderRouter(self, old_dp_val)]
                    elif settings.get(setting) is not None and old_dp_val is None:
                        # ловит ситуации =, когда свитч станет бордер либо свитч сменит номер бордер интерфейса
                        events += [c_ev.NewBorderRouter(self, old_dp_val)]
        #нужна для отлова ситуаций, когда 
        except TypeError as e: #
            print('DPconfig Type Error ', e)
            return []
        return events

    
    def add_port(self, port):
        """возвращает список ивентов для настройки нового порта, который уже указан в DatapathConfig"""
        events = []
        #если порт указан в конфигурации
        if port.num in self.ports.keys():
            #шлем сообщения, что порт надо сконфигурить(DatapathConfig, Port). Каждый модуль ловит такое сообщение и на основании нужных ему данных объекта Порт, конфигруит его
            events += [c_ev.PortUp(self, port)]
            #add port to DatapathConfig.ports
            self.ports[port.num] = port
        return events


    def change_port(self, port, old_port):
        """возвращает список ивентов для изменения настроек порта, который уже указан в DatapathConfig"""
        events = []
        #шлем сообщения, что порт надо сконфигурить(DatapathConfig, Port). Каждый модуль ловит такое сообщение и на основании нужных ему данных объекта Порт, конфигруит его
        events += port.configure(self, old_port)
        return events


    def del_port(self, port):
        """возвращает список ивентов для удаления порта"""
        events = []
        events += [c_ev.PortDown(self, port)]
        if port.num in self.ports.keys():
            #if port is in DPconfig - delete port from DatapathConfig.ports
            del self.ports[port.num]
        return events

    def generate_mac(self):
        mac = '02:00:00:%02x:%02x:%02x' % (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        while mac in self.announced_gws.keys() or mac in self.other_gws.keys():
            mac = '02:00:00:%02x:%02x:%02x' % (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        return mac

    def get_ospf_port(self):
        if self.ospf_out is not None:
            return self.ospf_out
        else:
            return None