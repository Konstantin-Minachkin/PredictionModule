# -*- coding: utf-8 -*-

from ryu.controller import event 


class PortUp(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(PortUp, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

    # @property
    # def table_name(self):
    #     return self.table

class PortDown(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(PortDown, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

class PortBlocked(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(PortBlocked, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

class NewDp(event.EventBase):
    def __init__(self, dp):
        super(NewDp, self).__init__()
        self.dp = dp

    def __str__(self):
        return '%s<Datapath=%s>' % (self.__class__.__name__, self.dp)

class LostDp(event.EventBase):
    def __init__(self, dp):
        super(LostDp, self).__init__()
        self.dp = dp

    def __str__(self):
        return '%s<Datapath=%s>' % (self.__class__.__name__, self.dp)        

class StartBackground(event.EventBase):
    def __init__(self):
        super(StartBackground, self).__init__()

    def __str__(self):
        return '%s' % (self.__class__.__name__)  
         

class NewPortNativeVlan(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(NewPortNativeVlan, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)


class NewPortTaggedVlan(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(NewPortTaggedVlan, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

class NewAnnouncedGw(event.EventBase):
    def __init__(self, dp_config_obj, int_ip, mac):
        super(NewAnnouncedGw, self).__init__()
        self.dp = dp_config_obj
        self.mac = mac
        self.int_ip = int_ip

    def __str__(self):
        return '%s<Datapath=%s  Mac=%s Interface=%s>' % (self.__class__.__name__, self.dp, self.int_ip, self.mac)

class NewGw(event.EventBase):
    def __init__(self, dp_config_obj, int_ip, mac):
        super(NewGw, self).__init__()
        self.dp = dp_config_obj
        self.mac = mac
        self.int_ip = int_ip

    def __str__(self):
        return '%s<Datapath=%s  Mac=%s Interface=%s>' % (self.__class__.__name__, self.dp, self.int_ip, self.mac)

class DelAnnouncedGw(event.EventBase):
    def __init__(self, dp_config_obj, int_ip, mac):
        super(DelAnnouncedGw, self).__init__()
        self.dp = dp_config_obj
        self.mac = mac
        self.int_ip = int_ip

    def __str__(self):
        return '%s<Datapath=%s  Mac=%s Interface=%s>' % (self.__class__.__name__, self.dp, self.int_ip, self.mac)

class DelGw(event.EventBase):
    def __init__(self, dp_config_obj, int_ip, mac):
        super(DelGw, self).__init__()
        self.dp = dp_config_obj
        self.mac = mac
        self.int_ip = int_ip

    def __str__(self):
        return '%s<Datapath=%s  Mac=%s Interface=%s>' % (self.__class__.__name__, self.dp, self.int_ip, self.mac)

class NewBorderRouter(event.EventBase):
    def __init__(self, dp_conf, old_dp_conf):
        super(NewBorderRouter, self).__init__()
        self.old_dp_conf = old_dp_conf
        self.dp_conf = dp_conf

    def __str__(self):
        return '%s<Datapath=%s OldDatapath=%s>' % (self.__class__.__name__, self.dp_conf, self.old_dp_conf)

class DelBorderRouter(event.EventBase):
    def __init__(self, dp_conf, old_dp_conf):
        super(DelBorderRouter, self).__init__()
        self.old_dp_conf = old_dp_conf
        self.dp_conf = dp_conf

    def __str__(self):
        return '%s<Datapath=%s OldDatapath=%s>' % (self.__class__.__name__, self.dp_conf, self.old_dp_conf)

class PortNeedClean(event.EventBase):
    def __init__(self, dp, port):
        super(PortNeedClean, self).__init__()
        self.dp = dp
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

class PortStateChanged(event.EventBase):
    def __init__(self, dp_num, port, old_state):
        super(PortStateChanged, self).__init__()
        self.dp_num = dp_num
        self.port = port
        self.old_state = old_state

    def __str__(self):
        return '%s<Datapath=%s  Port=%s   OldState=%s>' % (self.__class__.__name__, self.dp_num, self.port, self.old_state)

class VlRouteChange(event.EventBase):
    def __init__(self, r_id, new_route, old_route):
        super(VlRouteChange, self).__init__()
        self.r_id = r_id
        self.new_route = new_route
        self.old_route = old_route

    def __str__(self):
        return '%s<RouterId=%s  NewRoutes=%s   OldRoutes=%s>' % (self.__class__.__name__, self.r_id, self.new_route, self.old_route)

class VlRouteDelete(event.EventBase):
    def __init__(self, r_id, route):
        super(VlRouteDelete, self).__init__()
        self.r_id = r_id
        self.route = route

    def __str__(self):
        return '%s<RouterId=%s  Routes=%s>' % (self.__class__.__name__, self.r_id, self.route)

class VlRouteNew(event.EventBase):
    def __init__(self, r_id, route):
        super(VlRouteNew, self).__init__()
        self.r_id = r_id
        self.route = route

    def __str__(self):
        return '%s<RouterId=%s  Routes=%s>' % (self.__class__.__name__, self.r_id, self.route)


class AclChanged(event.EventBase):
    def __init__(self, acl_name, new_acl, old_acl):
        super(AclChanged, self).__init__()
        self.acl_name = acl_name
        self.new_acl = new_acl
        self.old_acl = old_acl

    def __str__(self):
        return '%s<Acl_Name=%s  NewAcl=%s   OldAcl=%s>' % (self.__class__.__name__, self.acl_name, self.new_acl, self.old_acl)

class DelAcl(event.EventBase):
    def __init__(self, acl_name, acl):
        super(DelAcl, self).__init__()
        self.acl_name = acl_name
        self.acl = acl

    def __str__(self):
        return '%s<Acl_Name=%s  Acl=%s>' % (self.__class__.__name__, self.acl_name, self.acl)

class NewAcl(event.EventBase):
    def __init__(self, acl_name, acl):
        super(NewAcl, self).__init__()
        self.acl_name = acl_name
        self.acl = acl

    def __str__(self):
        return '%s<Acl_Name=%s  Acl=%s>' % (self.__class__.__name__, self.acl_name, self.acl)

class AclInChanged(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(AclInChanged, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

class AclOutChanged(event.EventBase):
    def __init__(self, dp_config_obj, port):
        super(AclOutChanged, self).__init__()
        self.dp = dp_config_obj
        self.port = port

    def __str__(self):
        return '%s<Datapath=%s  Port=%s>' % (self.__class__.__name__, self.dp, self.port)

class VlanChanged(event.EventBase):
    def __init__(self, vlan_name, new_vlan, old_vlan):
        super(VlanChanged, self).__init__()
        self.vlan_name = vlan_name
        self.new_vlan = new_vlan
        self.old_vlan = old_vlan

    def __str__(self):
        return '%s<Vlan=%s  NewVlan=%s   OldVlan=%s>' % (self.__class__.__name__, self.vlan_name, self.new_vlan, self.old_vlan)

class DelVlan(event.EventBase):
    def __init__(self, vlan_name, vlan):
        super(DelVlan, self).__init__()
        self.vlan_name = vlan_name
        self.vlan = vlan

    def __str__(self):
        return '%s<Vlan_Name=%s  Vlan=%s>' % (self.__class__.__name__, self.vlan_name, self.vlan)

class NewVlan(event.EventBase):
    def __init__(self, vlan_name, vlan):
        super(NewVlan, self).__init__()
        self.vlan_name = vlan_name
        self.vlan = vlan

    def __str__(self):
        return '%s<Vlan_Name=%s  Vlan=%s>' % (self.__class__.__name__, self.vlan_name, self.vlan)

class getVlantoPorts(event.EventBase):
    def __init__(self, dp, code, data = {} ):
        super(getVlantoPorts, self).__init__()
        self.dp = dp
        self.code = code
        self.data = data

    def __str__(self):
        return '%s<Dp=%s Code=%s Data=%s>' % (self.__class__.__name__, self.dp, self.code, self.data)

class giveVlantoPorts(event.EventBase):
    def __init__(self, dp, vlan_to_ports, code, data = {}):
        super(giveVlantoPorts, self).__init__()
        self.dp = dp
        self.vlan_to_ports = vlan_to_ports
        self.code = code
        self.data = data

    def __str__(self):
        return '%s<Dp_num=%s vlan_to_ports=%s Code=%s Data=%s>' % (self.__class__.__name__, self.dp, self.vlan_to_ports, self.code, self.data )

# class NeighMacChanged(event.EventBase):
#     def __init__(self, dp_id, neigh_ip, mac):
#         super(NeighMacChanged, self).__init__()
#         self.dp_id = dp_id
#         self.neigh_ip = neigh_ip
#         self.mac = mac

#     def __str__(self):
#         return '%s<Dp_num=%s neigh_ip=%s Mac=%s>' % (self.__class__.__name__, self.dp_id, self.neigh_ip, self.mac )

class NewPredFlows(event.EventBase):
    def __init__(self, packet ,cookie):
        super(NewPredFlows, self).__init__()
        self.predicted_packet = packet
        self.cookie = cookie

    def __str__(self):
        return '%s<predicted_packet=%s  cookie=%s>' % (self.__class__.__name__, self.predicted_packet, self.cookie)

class NewPredFlows_part2(event.EventBase):
    def __init__(self, dp_obj, port_num, mac_src):
        super(NewPredFlows_part2, self).__init__()
        self.dp_obj = dp_obj
        self.port_num = port_num
        self.mac_src = mac_src

    def __str__(self):
        return f'{self.__class__.__name__} : <dp_obj={self.dp_obj}  port_num={self.port_num} mac_src={self.mac_src}>'


class ForPredictionEventOFPPacketIn(event.EventBase):
    def __init__(self, event):
        super(ForPredictionEventOFPPacketIn, self).__init__()
        self.event = event

    def __str__(self):
        return f'{self.__class__.__name__} : <ev={self.event}>'