# -*- coding: utf-8 -*-

from ryu.lib.packet import packet, ethernet, ether_types, ospf, ipv4

def add_flow(datapath, table_id, priority, match, actions, buffer_id=None, cookie = 4096, goto_table = None):
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
    if goto_table:
        inst += [parser.OFPInstructionGotoTable(goto_table)]
    if buffer_id:
        mod = parser.OFPFlowMod(datapath=datapath, cookie=cookie, table_id=table_id, priority=priority, buffer_id=buffer_id, match=match, instructions=inst)
    else:
        mod = parser.OFPFlowMod(datapath=datapath, cookie=cookie, table_id=table_id,priority=priority, match=match, instructions=inst)
    datapath.send_msg(mod)


def ospf_hello(dp, mac_src, ip_src, out_ports, router_id='0.0.0.0', area_id='0.0.0.0', mask='0.0.0.0', hello_interval=10, options=0, priority=1, dead_interval=40, designated_router='0.0.0.0', backup_router='0.0.0.0', neighbors=None):
    e = ethernet.ethernet(src=mac_src, dst='01:00:5e:00:00:05')
    ip = ipv4.ipv4 (proto=89, src=ip_src, dst='224.0.0.5', ttl = 1)
    #у пакетов оспф 24 байтный заголовок
    os = ospf.OSPFHello(router_id=router_id, area_id=area_id, mask=mask, hello_interval=hello_interval, options=options, priority=priority, dead_interval=dead_interval, designated_router=designated_router, backup_router=backup_router, neighbors=neighbors, au_type=0)
    p = packet.Packet()
    p.add_protocol(e)
    p.add_protocol(ip)
    p.add_protocol(os)
    p.serialize()
    packet_output(p, out_ports, dp)

def ospf_upd(dp, mac_src, ip_src, out_ports, router_id='0.0.0.0', area_id='0.0.0.0', mac_dst='01:00:5e:00:00:05', ip_dst='224.0.0.5', lsas = None):
    e = ethernet.ethernet(src=mac_src, dst=mac_dst)
    ip = ipv4.ipv4 (proto=89, src=ip_src, dst=ip_dst, ttl = 1)
    os = ospf.OSPFLSUpd(router_id=router_id, area_id=area_id, au_type=0, lsas = lsas)
    p = packet.Packet()
    p.add_protocol(e)
    p.add_protocol(ip)
    p.add_protocol(os)
    p.serialize()
    packet_output(p, out_ports, dp)
    

def ospf_advertise(dp, mac_src, ip_src, out_ports, router_id='0.0.0.0', area_id='0.0.0.0', mac_dst='01:00:5e:00:00:05', ip_dst='224.0.0.5', dd = 0, lsa_headers = None, m_flag = 0, options = 0x0, i_flag = 0, ms_flag = 0):
    e = ethernet.ethernet(src=mac_src, dst=mac_dst)
    ip = ipv4.ipv4 (proto=89, src=ip_src, dst=ip_dst, ttl = 1)
    os = ospf.OSPFDBDesc(router_id=router_id, area_id=area_id, lsa_headers=lsa_headers, au_type=0, sequence_number = dd, m_flag = m_flag, i_flag = i_flag, ms_flag = ms_flag, options = options)
    print(os)
    p = packet.Packet()
    p.add_protocol(e)
    p.add_protocol(ip)
    p.add_protocol(os)
    p.serialize()
    print()
    packet_output(p, out_ports, dp)

def ospf_lsack(dp, mac_src, ip_src, out_ports, headers = None, router_id='0.0.0.0', area_id='0.0.0.0', mac_dst='01:00:5e:00:00:05', ip_dst='224.0.0.5'):
    e = ethernet.ethernet(src=mac_src, dst=mac_dst)
    ip = ipv4.ipv4 (proto=89, src=ip_src, dst=ip_dst, ttl = 1)
    if headers != None and headers != []:
        os = ospf.OSPFLSAck(router_id=router_id, area_id=area_id, au_type=0, lsa_headers = headers)
        p = packet.Packet()
        p.add_protocol(e)
        p.add_protocol(ip)
        p.add_protocol(os)
        print(p)
        p.serialize()
        packet_output(p, out_ports, dp)

def packet_output(packet, out_ports, dp):
    ofproto = dp.ofproto
    parser = dp.ofproto_parser
    actions = []
    for port in out_ports:
        actions+=[parser.OFPActionOutput(port)]
    out = parser.OFPPacketOut(datapath=dp, buffer_id=ofproto.OFP_NO_BUFFER, in_port=ofproto.OFPP_CONTROLLER, actions=actions, data=packet.data)
    dp.send_msg(out)
