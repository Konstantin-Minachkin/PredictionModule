# -*- coding: utf-8 -*-

from helper_methods import props


class Acl:

    def __init__(self, name, settings):
        self.name = name
        self.rules = []
        for rule in settings:
            self.rules.append(Rule(rule.get('rule')))
        


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
                # if isinstance(getattr(other, prop)[0], Rule):
                #     for 
                #     old_p = getattr(other, prop).get(key)
                #     new_p = getattr(self, prop).get(key)
                #     #если порты не равны
                #     if new_p != old_p or (new_p is not None and old_p is None) or (old_p is None and new_p is not None):
                #         return False
                # else:
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
        args.append('<Acl')
        for prop in props(self):
            if isinstance(getattr(self, prop), dict):
                #взять каждый элемент новых настроек
                for key in getattr(self, prop).keys():
                    args.append('\n%s = %s' % (prop, getattr(self, prop).get(key)))
            elif isinstance(getattr(self, prop), list):
                args.append('\n%s = [' % prop)
                #взять каждый элемент новых настроек
                for el in getattr(self, prop):
                    args.append('\n%s' % el)
                args.append('\n]')
            else:
                args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ' '.join(args)

    def get_rule_priority(self, rule):
        return self.rules.index(rule)


class Rule:

    def __init__(self, settings):
        self.match = settings.get('match')
        self.actions = settings.get('actions')


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

    def __hash__(self):
        try:
            mhash = hash(frozenset(self.match))
        except TypeError:
            mhash = 1
        try:
            ahash = hash(frozenset(self.actions))
        except TypeError:
            ahash = 1
        return  mhash ^ ahash


    def __str__(self):
        args = []
        args.append('<AclRule')
        for prop in props(self):
            args.append('\n%s = '%prop)
            if isinstance(getattr(self, prop), dict):
                for key in getattr(self, prop).keys():
                    args.append('\n%s = %s' % (key, getattr(self, prop).get(key)))
            else:
                args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ' '.join(args)