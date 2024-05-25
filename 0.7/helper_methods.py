# -*- coding: utf-8 -*-
from ryu.lib.packet import ethernet, ether_types as ether, packet
from ryu.ofproto import ofproto_v1_3 as ofp
from ryu.ofproto import ofproto_v1_3_parser as parser
import hashlib
from ryu.lib.packet import packet, ethernet, arp, vlan

#contain methods that can be used by different classes and apps

def send_msgs(dp, msgs):
    "Send all the messages provided to the datapath"
    if bool(msgs):
        # print('msgs start to send\n')
        for msg in msgs:
            # print("!  ", dp.id, msg)
            # print()
            # print()
            dp.send_msg(msg)
        # print('End of send')

def send_l3_msgs(msgs):
    # структура msgs = {dp:[msgs]}
    for dp in msgs.keys():
        if bool(msgs[dp]):
            for msg in msgs[dp]:
                dp.send_msg(msg)


def make_message (datapath, cookie, table_id, priority, match, instructions = None, actions = None, buffer_id=None, command = None, idle_timeout = 0, hard_timeout = 0):
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    inst = []
    if actions is not None:
        inst += [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
    if instructions is not None:
        inst += instructions
    
    if command is None:
        command = ofproto.OFPFC_ADD
    if buffer_id:
        msg = parser.OFPFlowMod(datapath=datapath, cookie=cookie, table_id=table_id, priority=priority, buffer_id=buffer_id, match=match, instructions=inst, command = command, idle_timeout = idle_timeout, hard_timeout = hard_timeout)
    else:
        msg = parser.OFPFlowMod(datapath=datapath, cookie=cookie, table_id=table_id,priority=priority, match=match, instructions=inst, command = command, idle_timeout = idle_timeout, hard_timeout = hard_timeout)
    return msg

    
def del_flow(dp, cookie, table_id = None, match = None, out_port=None, out_group=None, priority=32768, actions = None, instructions = None, idle_timeout = 0, hard_timeout = 0):
    parser = dp.ofproto_parser
    ofp = dp.ofproto
    if out_port is None:
        out_port = ofp.OFPP_ANY
    if out_group is None:
        out_group = ofp.OFPG_ANY
    if table_id is None:
        table_id = ofp.OFPTT_ALL
    inst = []
    if actions is not None:
        inst += [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
    if instructions is not None:
        inst += instructions
    msg = parser.OFPFlowMod(cookie=cookie, cookie_mask=0xFFFFFFFFFFFFFFFF, datapath=dp,  table_id=table_id, command=ofp.OFPFC_DELETE, out_port=out_port, out_group=out_group, match = match, priority=priority, instructions = inst, idle_timeout = idle_timeout, hard_timeout = hard_timeout)
    return msg

def port_up(port, datapath):
    if port.mac is None:
        #значит такого интерфейса не сущетсвует
        return []
    proto= datapath.ofproto
    mask_all = (proto.OFPPC_PORT_DOWN | proto.OFPPC_NO_RECV | proto.OFPPC_NO_FWD | proto.OFPPC_NO_PACKET_IN)
    #hw_addr=hw_addr, 
    return [parser.OFPPortMod(datapath, port_no=port.num, config=0, mask=mask_all, hw_addr = port.mac)]  # 0 means "up" state = no flag configured

def port_shut(port, datapath):
    if port.mac is None:
        return []
    proto= datapath.ofproto
    #hw_addr=hw_addr, 
    return [parser.OFPPortMod(datapath, port_no=port.num, mask=(proto.OFPPC_PORT_DOWN), config=proto.OFPPC_PORT_DOWN, hw_addr = port.mac)]

def goto_table(table_id):
    "Generate an OFPInstructionGotoTable message"
    return parser.OFPInstructionGotoTable(table_id)

def apply_actions(dp, actions):
    "Generate an OFPInstructionActions message with OFPIT_APPLY_ACTIONS"
    return dp.ofproto_parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)


def action_output(dp, port, max_len=None):
    "Generate an OFPActionOutput message"
    kwargs = {'port': port}
    if max_len != None:
        kwargs['max_len'] = max_len
    return dp.ofproto_parser.OFPActionOutput(**kwargs)


def match(dp, in_port=None, eth_dst=None, eth_src=None, eth_type=None, **kwargs):
    "Generate an OFPMatch message"
    if in_port != None:
        kwargs['in_port'] = in_port
    if eth_dst != None:
        kwargs['eth_dst'] = eth_dst
    if eth_src != None:
        kwargs['eth_src'] = eth_src
    if eth_type != None:
        kwargs['eth_type'] = eth_type
    return dp.ofproto_parser.OFPMatch(**kwargs)


def barrier_request(dp):
    """Generate an OFPBarrierRequest message
    Used to ensure all previous flowmods are applied before running the
    flowmods after this request. For example, make sure the flowmods that
    delete any old flows for a host complete before adding the new flows.
    Otherwise there is a chance that the delete operation could occur after
    the new flows are added in a multi-threaded datapath.
    """
    return [dp.ofproto_parser.OFPBarrierRequest(datapath=dp)]

def props(cls):
    #get all Class properties
    return [i for i in cls.__dict__.keys() if i[:1] != '_']


def hash_for(data):
    # Prepare the project id hash
    hashId = hashlib.md5()
    hashId.update(repr(data).encode('utf-8'))
    return hashId.hexdigest()

def get_key(d, value):
    #получить ключ по значению в словаре
    for k, v in d.items():
        if v == value:
            return k
    return None

# @functools.lru_cache(maxsize=1024)
def arp_request( src_mac, src_ip, dst_ip, vid = None):
    src_ip = str(src_ip)
    dst_ip = str(dst_ip)
    BROADCAST = 'ff:ff:ff:ff:ff:ff'
    # BROADCAST = '00:00:00:00:00:00'
    e = ethernet.ethernet(src=src_mac, dst=BROADCAST, ethertype = 0x806)
    a = arp.arp(opcode=arp.ARP_REQUEST, src_mac=src_mac, src_ip=src_ip, dst_mac=BROADCAST, dst_ip=dst_ip)
    
    p = packet.Packet()

    if vid is not None:
        # 0x8100 - vlan ethertype
        vl_e = ethernet.ethernet(src=src_mac, dst=BROADCAST, ethertype = 0x8100)
        vl = vlan.vlan(vid=vid, ethertype=0x806)
        p.add_protocol(vl_e)
        p.add_protocol(vl)
    else:
        p.add_protocol(e)
    p.add_protocol(a)
    p.serialize()
    return p


def arp_reply(dp, out_ports, src_mac, src_ip, dst_mac, dst_ip, vid = None):
    src_ip = str(src_ip)
    dst_ip = str(dst_ip)
    p = packet.Packet()
    print(dp, out_ports, src_mac, src_ip, dst_mac, dst_ip)
    e = ethernet.ethernet(src=src_mac, dst=dst_mac, ethertype = 0x806)
    a = arp.arp(opcode=2, src_mac=src_mac, src_ip=src_ip, dst_mac=dst_mac, dst_ip=dst_ip)
    if vid is not None:
        # 0x8100 - vlan ethertype
        vl_e = ethernet.ethernet(src=src_mac, dst=dst_mac, ethertype = 0x8100)
        vl = vlan.vlan(vid=vid, ethertype=0x806)
        p.add_protocol(vl_e)
        p.add_protocol(vl)
    else:
        p.add_protocol(e)
    p.add_protocol(a)
    p.serialize()
    return packet_output(p, out_ports, dp)

def packet_output(packet, out_ports, dp):
    ofproto = dp.ofproto
    parser = dp.ofproto_parser
    actions = []
    for port in out_ports:
        actions+=[parser.OFPActionOutput(port)]
    return [parser.OFPPacketOut(datapath=dp, buffer_id=ofproto.OFP_NO_BUFFER, in_port=ofproto.OFPP_CONTROLLER, actions=actions, data=packet)]

