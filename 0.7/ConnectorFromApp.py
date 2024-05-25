# -*- coding: utf-8 -*-


import ofp_custom_events as c_ev
import yaml, pika
from Datatypes.deserialize import decode_predicted_packet_protobuff

from ryu.base import app_manager
from ryu.ofproto import ofproto_v1_3
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
# from ryu.controller import ofp_event
import table

import time

from webob import Response
from Datatypes import PredictedPackage_pb2
url = '/prediction/predicted_pkt'


CONFIG_PATH_PREDICTION_SERVICE = 'settings.yml'
LISTEN_INTERVAL = 60

instance_name = 'prediction_api_app'

class ConnectorFromApp(app_manager.RyuApp):
    # connection app that sends smth to PredictionService

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    
    _CONTEXTS = {
        'tables': table.Tables,
        'wsgi': WSGIApplication
        }
    
    _EVENTS = [c_ev.NewPredFlows]

    def __init__(self, *args, **kwargs):
        super(ConnectorFromApp, self).__init__(*args, **kwargs)
        self.interval = LISTEN_INTERVAL
        self.tables = kwargs['tables']
        self.log_table, self.log_table_id = self.tables.get_table('prediction_log')

        wsgi = kwargs['wsgi']
        wsgi.register(PredRestApp, {instance_name: self})


class PredRestApp(ControllerBase):

    def __init__(self, req, link, data, **config):
        super(PredRestApp, self).__init__(req, link, data, **config)
        self.pred_rest_app = data[instance_name]

    def encode_log_flag(self, ser_num, log_num):
        # для номера серии выделим больше памяти, чем для номера пердсказания в серии
        # max log_num = 8 bit = 255        # cookie_bits = 64         # log_bits = 8
        cookie = (ser_num << 8 & 0xFFFFFFFFFFFFFF00)+ (log_num & 0x00000000000000FF)
        return cookie

    @route('prediction', url, methods=['PUT'])
    def on_consume_prediction(self, req, **kwargs):
        # print('@@Info: Got req ', req)
        # print('@@Info: Got req.body  ', req.body )
        data = req.body
        packet = PredictedPackage_pb2.PredictedPackage()
        packet.ParseFromString(data)

        if packet is None:
            print('Warning ConnectorFromApp: wrong packet for on_consume! Skipped')
            return Response(status=500)

        cookie = self.encode_log_flag(packet.log_flag_series_num, packet.log_flag)

        event = c_ev.NewPredFlows(packet = packet, cookie = cookie)
        print(f'Sending an event {event}')
        self.pred_rest_app.send_event_to_observers(event)
        return Response(status=200)

