import argparse, yaml

import pika, psycopg2
import sys
sys.path.append('/home/control/Documents/PredictionService/')
from Datatypes import Package_pb2, countMsg_pb2, Predictor_pb2
from Datatypes.deserialize import decode_packet_protobuff

import ipaddress

class WriterM:

    def __init__(self, config) -> None:
        self.pg_connection_dict = {
            'dbname': config["dbConnectionString"]["dbname"],
            'user': config["dbConnectionString"]["user"],
            'password': config["dbConnectionString"]["passw"],
            'port': config["dbConnectionString"]["port"],
            'host': config["dbConnectionString"]["host"]
        }

        self.rbmq_conn_dict = {
            'credentials': pika.PlainCredentials(config["rabbitConnectionString"]["user"], config["rabbitConnectionString"]["passw"]),
            'host': config["rabbitConnectionString"]["host"],
            'port': config["rabbitConnectionString"]["port"],
            'virtual_host': config["rabbitConnectionString"]["vhost"]
        }


    def consume_savePckt(self, ch, method, props, body):
        # пишет пакет в БД и обрабатывает его дальше при необходимости 
        packet = decode_packet_protobuff(body, props)
        if packet is None:
            print('@@Warning: wrong packet for consume_savePckt! Skipped')
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
    
        print(f'@@Info: Got packet {packet}\n')

        # если номер порта = 0 - значит это не tcp\udp - можно не обрабатывать пакет
        if Package_pb2.portType.Name(packet.port_type) == 'none' or packet.port == 0:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        self.savePacket(packet)

        if packet.log_flag_series_num == 0 and packet.log_flag == 0:
            # выбрать подходящий предиктор из БД
            predictor_pkey = self.find_best_predictor(packet)
            if predictor_pkey is None:
                # print(f"@@Error: no predictor found for this packet Packet = {packet} Predictor_pkey={predictor_pkey}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            # теперь надо определить, какое действие мы с ним делаем
            with psycopg2.connect(**self.pg_connection_dict) as conn:
                with conn.cursor() as cur:
                    pred_pkey = f"pType_ip_src='{predictor_pkey[0]}' and pType_ip_dst='{predictor_pkey[1]}' and pType_port_group_id='{predictor_pkey[2]}' and specific_ip_dst='{predictor_pkey[3]}'"
                    cur.execute(f'''SELECT use_model, accuracy, validation_accuracy, model_file_url, scaler_file_url
                                     FROM Package_Predictor WHERE {pred_pkey} ''') 
                    result = cur.fetchall()
                    
                    if len(result)==0 or result[0][0] is None:
                        print(f"@@Error: in consume_savePckt. No use_model for predictor {pred_pkey} Packet = {packet}")
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                        return

                    result = result[0]
                    use_model = result[0]
                    curr_acc = result[1]
                    val_acc = result[2]

                    model_url = result[3]
                    scaler_url = result[4]

                    predictor = Predictor_pb2.Predictor()
                    predictor.src = predictor_pkey[0]
                    predictor.dst = predictor_pkey[1]
                    predictor.port_group_id = predictor_pkey[2]
                    predictor.specific_ip_dst = predictor_pkey[3]

                    if use_model is None or val_acc is None or model_url is None or scaler_url is None:
                        # если для предиктора не задана модель или модель не обучена
                        # отправляем запрос на обучение модели. Перед этим запрососм - надо проверить, что данных для обучения достаточно
                        # при обучении модели - уже будет установлен use_model + проверено, что пакетов для обучения достаточно
                        # также сразу отправим запрос then_predict, если данных достаточно - сразу будем предсказывать 

                        #TODO вставить тут проверку на размер датасета > минимального??

                        ch.basic_publish( exchange='prediction_requests', routing_key='create', 
                                properties=pika.BasicProperties(headers={"proto":True, "then_predict": True}), 
                                body=predictor.SerializeToString(predictor)  #, "aftermethod": "create"
                        )
                    else:
                        # чтобы не отправлять запросы КАЖДЫЙ раз - проверяем, что нет предсказаний для этого пакета
                        cur.execute(f''' SELECT count (*) FROM Predicted_Package 
                                        WHERE predicted_by_pType_ip_src='{predictor.src}' and predicted_by_pType_ip_dst='{predictor.dst}' and   predicted_by_pType_port_group_id='{predictor.port_group_id}' and ip_dst='{predictor.specific_ip_dst}'
                                    and not was_predicted 
                                ''')
                        num = cur.fetchall()
                        num = num[0]
                        num = num[0] if num is not None else 0

                        if num < self.get_series_reprediction_num(cur):
                            ch.basic_publish( exchange='prediction_requests', routing_key='predict', 
                                    properties=pika.BasicProperties(headers={"proto":True}), 
                                    body=predictor.SerializeToString(predictor)  #, "aftermethod": "create"
                            )

        else:
            # если у пакета лог флаг
            with psycopg2.connect(**self.pg_connection_dict) as conn:
                with conn.cursor() as cur:
                    
                    # найти, какой предиктор предсказывал пришедший пакет
                    # считаем, что бест предиктор и предсказал этот пакет
                    #TODO может быть ситуация, что бест предиктор изменился на другой
                    predictor_pkey = self.find_best_predictor(packet)

                    if predictor_pkey is None:
                        print(f"@@Error: no predictor found for this packet Packet = {packet} Predictor_pkey={predictor_pkey}")
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                        return

                    #указать, что пакет верно предсказан в Predicted_Package для пакетов, чей лог флаг = лог флага пакета
                    pred_where_key = f"predicted_by_pType_ip_src='{predictor_pkey[0]}' and predicted_by_pType_ip_dst='{predictor_pkey[1]}' and predicted_by_pType_port_group_id='{predictor_pkey[2]}' and ip_dst='{predictor_pkey[3]}'"
                    cur.execute(f'''UPDATE Predicted_Package SET was_hited=true WHERE {pred_where_key} and log_flag={packet.log_flag} and log_flag_series_num={packet.log_flag_series_num} ''') 
                    
                    count_series_delay = self.get_count_series_delay(cur)

                    cur.execute(f'''SELECT counted_log_flag, counted_log_flag_series_num FROM Package_Predictor 
                            WHERE pType_ip_src='{predictor_pkey[0]}' and pType_ip_dst='{predictor_pkey[1]}' and pType_port_group_id='{predictor_pkey[2]}' and specific_ip_dst='{predictor_pkey[3]}'
                               ''') 
                    result = cur.fetchall()
                    result = result[0]


            if result[0] is None:
                print (f"Error: cant do consume_savePckt for {packet}! No counted_log_flag, counted_log_flag_series_num for Package_Predictor WHERE {pred_where_key}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            last_log_flag = result[0]
            last_series = result[1]
            # если пакет - последний в серии предсказанных (или уже из след серии), то запускаем расчет точности
            
            if packet.log_flag_series_num > (last_series + count_series_delay):
            # передать пакет, чтобы изменить точность + учесть все пакеты, которые не предугадались
                count_msg = countMsg_pb2.countMsg()
                count_msg.predictor_src = predictor_pkey[0]
                count_msg.predictor_dst = predictor_pkey[1]
                count_msg.predictor_port_group_id = predictor_pkey[2]
                count_msg.predictor_specific_ip_dst = predictor_pkey[3]
                count_msg.log_flag = packet.log_flag
                count_msg.log_flag_series_num = packet.log_flag_series_num

                ch.basic_publish( exchange='prediction_requests', routing_key='count', 
                                properties=pika.BasicProperties(headers={"proto":True}), 
                                body=count_msg.SerializeToString(count_msg) 
                        )


        ch.basic_ack(delivery_tag=method.delivery_tag)


    def find_best_predictor(self, packet):
        #ищет  для пакета подходящий предиктор, который нужно испоьзовать для предсказания
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cur:
                cur.execute(f''' with p as (SELECT * FROM Package 
                            WHERE ip_src=inet'{packet.ip_src}' and ip_dst=inet '{packet.ip_dst}' and port_type='{Package_pb2.portType.Name(packet.port_type)}' and port_num={packet.port} and time='{packet.timestap}')
                            SELECT pType_ip_src, pType_ip_dst, pType_port_group_id, p.ip_dst
                            from p LEFT JOIN Package_header_Package_Type pt ON pt.ip_src=p.ip_src and pt.ip_dst=p.ip_dst and pt.port_type=p.port_type and pt.port_num=p.port_num
                        ''')
                pType_list = cur.fetchall()
                # print(f"@@Info: find_best_predictor: pType_list={pType_list}")
                pType_ammount = len(pType_list)
                if pType_ammount == 1:
                    try:
                        predictor_pkey = [pType_list[0][0], pType_list[0][1], pType_list[0][2], pType_list[0][3]]
                    except IndexError as e:
                        print(f"@@Error!: Cant find appropirate predictor for {packet}")
                        return None
                    return predictor_pkey

                # ищем предиктор с наибольшей точностью
                pkey=self.with_best_acc(cur, packet)
                if pkey is None:
                    # ищем предиктор с наибольшей подсетью в ip_src
                    pkey = self.with_biggest_ip_src_num(cur, pType_list)
                return pkey


    def with_best_acc(self, cur, packet):
        cur.execute(f''' with p as (SELECT * FROM Package 
                                WHERE ip_src=inet'{packet.ip_src}' and ip_dst=inet '{packet.ip_dst}' and port_type='{Package_pb2.portType.Name(packet.port_type)}' and port_num={packet.port} and time='{packet.timestap}')
                            SELECT select pp.pType_ip_src, pp.pType_ip_dst, pp.pType_port_group_id, p.ip_dst, accuracy, validation_accuracy
                            from p LEFT JOIN Package_header_Package_Type pt ON pt.ip_src=p.ip_src and pt.ip_dst=p.ip_dst 
                                and pt.port_type=p.port_type and pt.port_num=p.port_num
                            LEFT JOIN Package_Predictor pp ON pp.pType_ip_src=pt.pType_ip_src and pp.pType_ip_dst=pt.pType_ip_dst 
                                and pp.pType_port_group_id=pt.pType_port_group_id and pp.specific_ip_dst=p.ip_dst
                            ORDER BY accuracy DESC validation_accuracy DESC LIMIT 1
                        ''')
        res = cur.fetchall()
        # print(f'@@Info: Best acc = {res}')
        pr = res[0]
        if pr[0] is None:
            return None
        return [pr[0], pr[1], pr[2], pr[3]]


    def with_biggest_ip_src_num(self, cur, pType_list):
        #ищет  для пакета подходящий предиктор, который нужно испоьзовать для предсказания
        # критерий - предиктор, у которого больше всего возможных хостов в датасете
        #TODO сделать вместо этого критерия так, чтобы по каждому предиктору с точностью null - запускался механизм обучения модели вначале - чтобы появился показатель точности
        host_num_for_subn = {} #pType_list index: hosts ammount for subnet filter from this index
        i = 0
        for pt in pType_list:
            if pt[0] == '0.0.0.0/0':
                print('#Got subnet 0.0.0.0/0, will use this ptype as predictor: ', pt)
                predictor_pkey = [pt[0], pt[1], pt[2], pt[3]]
                return predictor_pkey
            
            cur.execute(f'''select net_address from Ip_Subnet_ip_group where group_filter_string='{pt[0]}' ''')
            subn_list = cur.fetchall()
            # print(f"@@Info: biggest ip - {subn_list}")
            total = 0
            for subn in subn_list:
                total+= ipaddress.IPv4Network(subn).num_addresses()
            
            host_num_for_subn[i]=total
            i+=1

        # sort by value
        sorted_subn = dict(sorted(host_num_for_subn.items(), key=lambda item: item[1]))
        # возвращаем тот pkey, где subnet filter string - наибольший
        index = next(iter(sorted_subn.keys())) #get first elem of dict
        predictor_pkey = [pType_list[index][0], pType_list[index][1], pType_list[index][2], pType_list[index][3]]
        return predictor_pkey



    def savePacket(self, packet):
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cur:
                cur.execute(f"INSERT into Port (port_num, port_type) values ({packet.port}, '{Package_pb2.portType.Name(packet.port_type)}') ON CONFLICT DO NOTHING")
                cur.execute(f"INSERT into Package_header(ip_src, ip_dst, port_type, port_num) \
                            values('{packet.ip_src}', '{packet.ip_dst}', '{Package_pb2.portType.Name(packet.port_type)}', {packet.port}) ON CONFLICT DO NOTHING")
                try:
                    cur.execute(f"INSERT into Package(time, ip_src, ip_dst, port_type, port_num, log_flag, log_flag_series_num) \
                                values('{packet.timestap}', '{packet.ip_src}', '{packet.ip_dst}', '{Package_pb2.portType.Name(packet.port_type)}', {packet.port}, {packet.log_flag}, {packet.log_flag_series_num})")
                except psycopg2.errors.UniqueViolation as e:
                    print(f"@@Error: got error. Tried to insert already existing packet packet={packet} error: {e}")
                    return

                # Получаем список всех типов пакета
                cur.execute(f'''select distinct pt.ip_src, pt.ip_dst, pt.port_group_id
                            FROM Package_Type as pt
                            LEFT JOIN Ip_Subnet_ip_group src ON src.group_filter_string = pt.ip_src
                            LEFT JOIN Ip_Subnet_ip_group dst ON dst.group_filter_string = pt.ip_dst
                            LEFT JOIN Port_Port_group pgr ON pgr.group_name = pt.port_group_id
                            WHERE inet '{packet.ip_src}' <<= src.net_address and inet '{packet.ip_dst}' <<= dst.net_address
                            and ( ('{Package_pb2.portType.Name(packet.port_type)}' = pgr.port_type and {packet.port} = pgr.port_num)
                                or ('tcp' = pgr.port_type and pt.port_group_id ='any-tcp') 
                                or ('udp' = pgr.port_type and pt.port_group_id ='any-udp') 
                                or (pt.port_group_id ='any') )
                            ;''')
                appropirate_types = cur.fetchall()
                # связываем этот пакет со всеми подходящими типами пакетов
                for t in appropirate_types:
                    pType_ip_src = t[0]
                    pType_ip_dst = t[1] 
                    pType_port_group_id = t[2]
                    cur.execute(f"INSERT into Package_header_Package_Type (pType_ip_src, pType_ip_dst, pType_port_group_id, ip_src, ip_dst, port_type, port_num) \
                        values('{pType_ip_src}', '{pType_ip_dst}', '{pType_port_group_id}', '{packet.ip_src}', '{packet.ip_dst}', '{Package_pb2.portType.Name(packet.port_type)}', {packet.port}) ON CONFLICT DO NOTHING ")


    def get_accuracy_deviation(self, cur):
        cur.execute(''' SELECT select_int_setting('min_accuracy') ''')
        res = cur.fetchall()
        res = res[0][0]
        res = res if res is not None else 99
        return res


    def get_count_series_delay(self, cur):
        cur.execute(''' SELECT select_int_setting('count_series_delay') ''')
        result = cur.fetchall()
        result = result[0][0]
        result = result if result is not None else 0
        return result

    def get_series_reprediction_num(self, cur):
        cur.execute(''' SELECT select_int_setting('series_reprediction_num') ''')
        result = cur.fetchall()
        result = result[0][0]
        result = result if result is not None else 99
        return result




def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-sf', '--settings_file', dest="settings_file", default='settings.yml', type=str) #путь до файла с настройками
    args = parser.parse_args()

    with open(str(args.settings_file)) as f:
        config = yaml.safe_load(f)

    Writer = WriterM(config=config)
    with pika.BlockingConnection(pika.ConnectionParameters(**Writer.rbmq_conn_dict)) as connection:
        channel = connection.channel()
        channel.basic_consume(queue='pkts_from_controller_write_reqs', on_message_callback=Writer.consume_savePckt)
        print(" [x] Awaiting requests")
        channel.start_consuming()



if __name__ == '__main__':
    main()
