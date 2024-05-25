# -*- coding: utf-8 -*-

import time
from helper_methods import props
from collections import defaultdict
from ipaddress import ip_interface

DEFAULT_DEAD_TIME = 3600 #сколько помнить хост в секундах (dead time)

class ArpCache:
    "Keeps track of recently learned hosts to prevent duplicate flowmods"

    def __init__(self):
        self.cache = defaultdict(dict) #{dp_id: { ip_interface:[ArpRecord] } }
        self.timeout = DEFAULT_DEAD_TIME

    def __str__(self):
        args = []
        args.append('<ArpCache')
        for prop in props(self):
            args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ''.join(args)

    def add_host(self, dpid, ip, port, mac):
        #check if the ip is already in some port, if on port - delete, add to new port
        self.check_time()
        dp = self.cache.get(dpid)
        if dp is not None:
            ip_adr = ip_interface(ip)
            record = self.cache[dpid].get(ip_adr)
            if record is not None:
                #обновляем арп запись
                record.timestamp = time.time()
            else:
                self.cache[dpid][ip_adr] = ArpRecord(mac, port)
        else:
            self.cache[dpid][ip_adr] = ArpRecord(mac, port)

    def check_time(self):
        "Clean entries older than self.timeout"
        curtime = time.time()
        for recds in self.cache.values():
            for ip, record in recds.items():
                if record.timestamp + self.timeout < curtime:
                    del recds[ip]
    
    def get_host(self, dp_id, ip):
        dp = self.cache.get(dp_id)
        record = None
        if dp is not None:
            record = self.cache[dp_id].get(mac)
        return record

    def get_all_dps(self, ip):
        #возвращает все {dp_id:port}, в которых есть этот ip
        dps = {}
        for dp, ipD in self.cache.keys():
            if mac in macD.keys():
                dps[dp] = macD[mac].port
                break
        return dps

class ArpRecord:
    def __init__(self, mac, port):
        self.mac = mac
        self.port = port
        self.timestamp = time.time()

    def __str__(self):
        args = []
        args.append('<ArpRecord')
        for prop in props(self):
            args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ''.join(args)