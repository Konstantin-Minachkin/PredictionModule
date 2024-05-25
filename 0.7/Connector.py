
import pika, requests

import sys, datetime
sys.path.append('/home/control/Documents/PredictionService/')
from Datatypes import Package_pb2

from Datatypes.deserialize import decode_packet_protobuff, decode_predicted_packet_protobuff


rbmq_conn_dict = {
            'credentials': pika.PlainCredentials('admin', 'qweqwe'),
            'host': 'localhost',
            'port': 5672,
            'virtual_host': 'prediction_service',
            'heartbeat': 60
}

def encode_log_flag(self, ser_num, log_num):
    # для номера серии выделим больше памяти, чем для номера пердсказания в серии
    # max log_num = 8 bit = 255        # cookie_bits = 64         # log_bits = 8
    cookie = (ser_num << 8 & 0xFFFFFFFFFFFFFF00)+ (log_num & 0x00000000000000FF)
    return cookie


def send_event(body):
    url = 'http://127.0.0.1:8080'+'/prediction/predicted_pkt'
    r = requests.put(url, data=body)

def on_consume(ch, method, props, body):
    print(f"ConnectorApp got message {body}")
    packet = decode_predicted_packet_protobuff(body, props)
    if packet is None:
        print('Warning ConnectorFromApp: wrong packet for on_consume! Skipped')
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    send_event(body = body)
    ch.basic_ack(delivery_tag=method.delivery_tag)

    
# расшифровываем пришедшее сообщение
with pika.BlockingConnection(pika.ConnectionParameters(**rbmq_conn_dict)) as connection:
    channel = connection.channel()
    channel.basic_consume(queue='predicted_pkts', on_message_callback=on_consume)
    print(" Connector app: Awaiting requests")
    channel.start_consuming()



