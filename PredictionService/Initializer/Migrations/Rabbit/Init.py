

from ..MigrationBase import migration
import pika

class InitRabbit():
    
    # если используем transation - то функции apply надо передавать не connection_obj, а connection_dictionary
    # возваращает false - если что-то пошло не так
    @migration
    # connection_obj is a connectionObject returned from psycopg2 connect()
    def apply(self, cursor,  rbmq_con, **kwargs):
        #подключиться к рэббит
        with pika.BlockingConnection(pika.ConnectionParameters(**rbmq_con)) as rabbit_con:
            channel = rabbit_con.channel()
            #создать в рэббите очереди\точки (удалить их если с таким именем уже существуют?)
            channel.exchange_declare(exchange='prediction_requests', exchange_type='direct', durable=True)
             
            channel.queue_declare(queue='create_model_reqs', durable=True)
            channel.queue_declare(queue='fit_model_reqs', durable=True)
            channel.queue_declare(queue='pkts_from_controller_write_reqs', durable=True)
            channel.queue_declare(queue='predict_pckts_reqs', durable=True)
            channel.queue_declare(queue='count_error_reqs', durable=True)
            channel.queue_declare(queue='clean_reqs', durable=True)
            
            channel.queue_bind(routing_key='create', queue='create_model_reqs', exchange='prediction_requests')
            channel.queue_bind(routing_key='fit', queue='fit_model_reqs', exchange='prediction_requests')
            channel.queue_bind(routing_key='save_pkt', queue='pkts_from_controller_write_reqs', exchange='prediction_requests')
            channel.queue_bind(routing_key='predict', queue='predict_pckts_reqs', exchange='prediction_requests')
            channel.queue_bind(routing_key='count', queue='count_error_reqs', exchange='prediction_requests')
            channel.queue_bind(routing_key='clean', queue='clean_reqs', exchange='prediction_requests')


            channel.exchange_declare(exchange='connection_reqs', exchange_type='direct', durable=True)

            channel.queue_declare(queue='predicted_pkts', durable=True)

            channel.queue_bind(routing_key='predicted', queue='predicted_pkts', exchange='connection_reqs')

