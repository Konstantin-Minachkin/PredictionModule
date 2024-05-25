# -*- coding: utf-8 -*-

from helper_methods import props

class Vlan:

    def __init__(self, name, settings):
        print(name, '  settings = ', settings)
        self.vid = settings.get('vid')
        self.name = name
        self.net = settings.get('net')
        # lists of acl names
        self.acl_in = settings.get('acl_in')
        self.acl_out = settings.get('acl_out') 

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
                old_p = set(getattr(other, prop))
                new_p = set(getattr(self, prop))
                #если значения в списке не равны
                if new_p != old_p:
                    return False
            #в ином случае просто сравнить значения
            elif getattr(self, prop) != getattr(other, prop):
                return False
        return True

    def __str__(self):
        args = []
        args.append('<Vlan')
        for prop in props(self):
            args.append('\n%s = '%prop)
            if isinstance(getattr(self, prop), dict):
                for key in getattr(self, prop).keys():
                    args.append('\n%s = %s' % (key, getattr(self, prop).get(key)))
            else:
                args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ' '.join(args)