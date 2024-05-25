# -*- coding: utf-8 -*-
import logging
import time
from helper_methods import props

from collections import defaultdict

class _HostCacheEntry(object):
    "Basic class to hold data on a cached host"

    def __init__(self, dpid, port, mac):
        self.dpid = dpid
        self.port = port
        self.mac = mac
        self.timestamp = time.time()
        self.counter = 0

    def __str__(self):
        args = []
        args.append('<HostCache')
        for prop in props(self):
            args.append(' %s = %s ' % (prop, getattr(self, prop)))
        args.append('>')
        return ''.join(args)

class HostCache(object):
    "Keeps track of recently learned hosts to prevent duplicate flowmods"

    def __init__(self, timeout):
        self.cache = defaultdict(lambda: defaultdict(dict))
        self.logger = logging.getLogger("SwHostCache")
        # The amount of time that the controller ignores packets matching a recently
        # learned dpid/port/mac combination. This is used to prevent the controller
        # application from processing a large number of packets forwarded to the
        # controller between the time the controller first learns a host and the
        # datapath has the appropriate flow entries fully installed.
        self.timeout = timeout

    def __str__(self):
        arg_str='<HostCache'
        for dpid, dvals in list(self.cache.items()):
            for port, pvals in list(dvals.items()):
                for mac, host in list(pvals.items()):
                    arg_str += f"[{dpid}][{port}][{mac}]={host}\n"
        arg_str += f'timeout={self.timeout} >'
        return arg_str

    def is_new_host(self, dpid, port, mac):
        "Check if the host/port combination is new and add the host entry"
        self.clean_entries()
        dp_n = self.cache.get(dpid, None)
        port_n = dp_n.get(port, None) if dp_n is not None else None
        entry = port_n.get(mac, None) if port_n is not None else None
        if entry != None:
            entry.timestamp = time.time()
            entry.counter += 1
            return False

        entry = _HostCacheEntry(dpid, port, mac)
        self.cache[dpid][port][mac] = entry
        # self.logger.debug("!!Learned %s, %s, %s", dpid, port, mac)
        return True

    def clean_entries(self):
        "Clean entries older than self.timeout"
        curtime = time.time()
        for dpid, dvals in list(self.cache.items()):
            for port, pvals in list(dvals.items()):
                for mac, host in list(pvals.items()):
                    if host is not None and (host.timestamp + self.timeout < curtime):
                        self.cache[dpid][port].pop(mac)
                        self.logger.debug("!!Unlearned %s, %s, %s after %s hits", host.dpid, host.port, host.mac, host.counter)