# -*- coding: utf-8 -*-

from helper_methods import props
import ofp_custom_events as c_ev

class Port:

    def __init__(self, num, settings):
        self.num = num
        #0 - down, 1 up. If none then up, 2 - block broadcast traffic from port
        self.state = settings.get('state')
        if self.state is None:
            self.state = 1
        self.mac = None
        self.speed = None 

        #храним только имена вланов, их vid будем получать из настроек конфиги уже в самом модуле
        self.tagged_vlans = settings.get('tagged_vlans')
        self.native_vlan = settings.get('native_vlan')
        if self.native_vlan is None and self.tagged_vlans is None:
            self.native_vlan = settings.get('dp_native_vlan')

        # lists of acl names
        self.acl_in = settings.get('acl_in')
        self.acl_out = settings.get('acl_out')

    
    def __str__(self):
        args = []
        args.append('<Port')
        for prop in props(self):
            if isinstance(getattr(self, prop), dict):
                for key in getattr(self, prop).keys():
                    args.append('\n%s = %s' % (prop, getattr(self, prop).get(key)))
            else:
                args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ' '.join(args)
    

    def __eq__(self, other):
        if (other is None and self is not None) or (self is None and other is not None):
            return False
        for prop in props(self):
            #если характиристика - словарь
            if isinstance(getattr(self, prop), dict):
                #взять каждый элемент новых настроек
                for key in getattr(self, prop).keys():
                    old_p = getattr(other, prop).get(key)
                    new_p = getattr(self, prop).get(key)
                    #если значения в словаре не равны
                    if new_p != old_p:
                        return False
            elif isinstance(getattr(self, prop), list):
                if set(getattr(self, prop)) != set(getattr(other, prop)):
                    return False
            #в ином случае просто сравнить значения
            elif getattr(self, prop) != getattr(other, prop):
                return False
        return True


    def configure(self, dp_config, old_p):
        """создает словарь натсроек, которые надо будет применить к порту
        возвращает список событий на основе этих настроек"""
        settings = {}
        for prop in props(self):
            #изменить настройки, основываясь на  нынешних и прошлых настройках, указанных в old_p
            #для обработки vlan
            if isinstance(getattr(self, prop), list):
                if prop == 'tagged_vlans' and getattr(old_p, prop) is None:
                    #обработка смены native -> tagged
                    settings[prop] = getattr(self, prop)
                elif set(getattr(self, prop)) != set(getattr(old_p, prop)):
                    #смена tagged -> tagged обрабатывается как все остальные листы
                    settings[prop] = getattr(self, prop)
            
            elif getattr(self, prop) != getattr(old_p, prop):
                #need to change settings to the current state
                if prop == 'mac' and getattr(self, prop) is None and getattr(old_p, prop) is not None:
                    self.mac = old_p.mac
                elif prop == 'speed' and getattr(self, prop) is None and getattr(old_p, prop) is not None:
                    self.speed = old_p.speed
                elif prop == 'tagged_vlans' and getattr(old_p, prop) is not None:
                    #обработка смены tagged -> native
                    continue
                elif prop == 'native_vlan' and getattr(self, prop) is None:
                    #обработка смены native -> tagged
                    continue
                else:
                    #смена native -> native обрабатывается как все остальные переменные
                    settings[prop] = getattr(self, prop)
        return self.apply_settings(dp_config, settings, old_p)



    def apply_settings(self, dp_config, settings, old_p):
        """создает из словаря настроек список ивентов
        settings - словарь характеристик которые надо установить {имя характеристики :значение}"""
        events = []
        try:
            for setting in settings.keys():
                setting_val = settings[setting]
                if setting == 'state':
                    #если порт был в блоке - сохраняем это состояние
                    # первостепенее не конфига, а настройки в stpapp, поэтому ориентируемся на олд-порт-стейт, при необходимости stpapp сам изменить состояние порта
                    if getattr(old_p, setting) == 2:
                        self.state = 2
                        # не делаем, тк stpapp сам пошлет сообщение
                        # # если порт по конфиге в апе
                        # if setting_val == 1:
                        #     # слать событие об изменении состояния порта
                        #     events += [c_ev.PortStateChanged(dp = s1, port = port, old_state = port.state)]
                        continue

                    if setting_val == 1:
                        # print('**********PortUp  ', self)
                        events += [c_ev.PortUp(dp_config, self)]
                    elif setting_val == 0:
                        # print('*********PortDown  ', self)
                        events += [c_ev.PortDown(dp_config, self)]
                    # elif setting_val == 2:
                        # сработат, если руками в конфиге увести порт в state2, те если до этого порт не был в state2
                        #не делаем, тк за работой state2 следит stpapp

                elif setting == 'mac':
                    self.mac = settings[setting]
                elif setting == 'speed':
                    self.speed = settings[setting]
                elif setting == 'native_vlan':
                    events += [c_ev.NewPortNativeVlan(dp_config, self)]
                elif setting == 'tagged_vlans':
                    events += [c_ev.NewPortTaggedVlan(dp_config, self)]
                elif setting == 'acl_in':
                    events += [c_ev.AclInChanged(dp_config, self)]
                elif setting == 'acl_out':
                    events += [c_ev.AclOutChanged(dp_config, self)]
        except TypeError as e:
            print('Port Type Error ', e)
            return []
        return events