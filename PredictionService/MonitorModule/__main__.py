import argparse, yaml

import pika, psycopg2
from time import sleep
import sys
sys.path.append('/home/control/Documents/PredictionService/')
from Datatypes import PredictedPackage_pb2, Predictor_pb2

from collections import defaultdict


SETTING_series_reprediction_num_EMERGENCY_VALUE = 1

class MonitorM:

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


    def monitor(self, cooldown):
        print('Start monitoring')
        while True:
            # get specific packages from DB - need packages that should be handled by controller
            with psycopg2.connect(**self.pg_connection_dict) as conn:
                with conn.cursor() as cur:
                    # тк добавили pkey_packagePredictor - то уже нет нужды выполнять этот запрос - все нужные данные уже в Predicted_Package есть
                    # cur.execute(f'''with p as ( SELECT * FROM Predicted_Package WHERE time<=( CURRENT_TIMESTAMP + 
                    #                     (select_int_setting('time_make_way') || ' second')::interval + 
                    #                     (select_int_setting('delay') || ' second')::interval ) and not was_predicted ),
                                    
                    #                     all_p as ( select p.ip_src, p.ip_dst, log_flag, p.port_type, p.port_num, predicted_time, 
                    #                         pType_ip_src, pType_ip_dst, pType_port_group_id    from p
                    #                         LEFT JOIN Package_header_Package_Type pt ON pt.ip_src=p.ip_src and pt.ip_dst=p.ip_dst and pt.port_type=p.port_type and pt.port_num=p.port_num 
                    #                     ) 
                    #             select * from all_p where pType_ip_src is not null and pType_ip_dst is not null and pType_port_group_id is not null
                    #             ''')

                    cur.execute('''SELECT ip_src, ip_dst, log_flag, port_type, port_num, predicted_time, predicted_by_pType_ip_src, predicted_by_pType_ip_dst, predicted_by_pType_port_group_id, log_flag_series_num
                                        FROM Predicted_Package WHERE predicted_time<=( CURRENT_TIMESTAMP + 
                                        (select_int_setting('time_make_way') || ' second')::interval + 
                                        (select_int_setting('delay') || ' second')::interval ) and not was_predicted
                                ''')
                                
                    handling_pckts = cur.fetchall()
                    if len(handling_pckts)==0 or handling_pckts[0][0] is None:
                        # пропускаем обработку, если ничего не пришло
                        sleep(cooldown)
                        continue
                    
                    cur.execute("select select_int_setting('series_reprediction_num')")
                    series_reprediction_num = cur.fetchall()
                    if series_reprediction_num[0][0] is None:
                        series_reprediction_num = SETTING_series_reprediction_num_EMERGENCY_VALUE
                    else:
                        series_reprediction_num = series_reprediction_num[0][0]
                    # создаем словарь словарей, чтобы хранить pkey предикторов, для которых нужно предсказать нвоые значения
                    pkts_to_repredict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

                    with pika.BlockingConnection(pika.ConnectionParameters(**self.rbmq_conn_dict)) as connection:
                        channel = connection.channel()
                        for pck in handling_pckts:
                            packet = PredictedPackage_pb2.PredictedPackage()
                            packet.ip_src = pck[0]
                            packet.ip_dst = pck[1]
                            packet.log_flag = pck[2]
                            packet.log_flag_series_num = pck[9]
                            packet.lifetime = self.get_time_make_way(cur) + self.get_delay(cur)
                            
                            channel.basic_publish( exchange='connection_reqs', routing_key='predicted', 
                                        properties=pika.BasicProperties(headers={"proto":True}), 
                                        body=packet.SerializeToString(packet))
                    
                            cur.execute(f'''UPDATE Predicted_Package SET was_predicted=true WHERE ip_src=inet'{pck[0]}' and 
                                                ip_dst=inet'{pck[1]}' and port_type='{pck[3]}' and port_num={pck[4]} and predicted_time='{pck[5]}'
                                        ''')
                            conn.commit()
                        
                            # check if package should be repredicted because predictions will end soon
                            cur.execute(f'''select count (*) from (
                                                    SELECT distinct on (log_flag, log_flag_series_num) log_flag,log_flag_series_num
                                                    FROM Predicted_Package 
                                                    WHERE ip_dst=inet'{pck[1]}' and predicted_by_pType_ip_src='{pck[6]}' and predicted_by_pType_ip_dst='{pck[7]}' and predicted_by_pType_port_group_id='{pck[8]}' 
                                                        and not was_predicted
                                                ) as a
                                    ''') #and log_flag>{pck[2]} and log_flag_series_num>={packet.log_flag_series_num} 
                            log_flags_remain = cur.fetchall()
                            # формируем массив предикторов, для которых надо предсказать
                            if log_flags_remain[0][0] <= series_reprediction_num:
                                print(f"\n@@Info: Модуль monitor для пакета {pck} будет предсказывать новую серию.")
                                print(f"@@Info: log_flags_remain={log_flags_remain} log_flags_remain[0][0]={log_flags_remain[0][0]} series_reprediction_num={series_reprediction_num}\n")
                                pkts_to_repredict[pck[6]][pck[7]][pck[8]][pck[1]] = True

                        # шлет запрос на предсказание доп значений (в теле сообщ передает pkey packagePredictor)
                        for src, x_val in pkts_to_repredict.items():
                            for dst, y_val in x_val.items():
                                for group_id, z_val in y_val.items():
                                    for ip_dst, k_val in z_val.items():
                                        predictor = Predictor_pb2.Predictor()
                                        predictor.src = src
                                        predictor.dst = dst
                                        predictor.port_group_id = group_id
                                        predictor.specific_ip_dst = ip_dst
                                        channel.basic_publish( exchange='prediction_requests', routing_key='predict', 
                                                    properties=pika.BasicProperties(headers={"proto":True}), 
                                                    body=predictor.SerializeToString(predictor))

            sleep(cooldown)

    def get_time_make_way(self, cur):
        cur.execute(''' SELECT select_int_setting('time_make_way') ''')
        res = cur.fetchall()
        res = res[0][0]
        res = res if res is not None else 0
        return res
    
    def get_delay(self, cur):
        cur.execute(''' SELECT select_int_setting('delay') ''')
        res = cur.fetchall()
        res = res[0][0]
        res = res if res is not None else 0
        return res



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-sf', '--settings_file', dest="settings_file", default='settings.yml', type=str) #путь до файла с настройками
    parser.add_argument('-t', '--cooldown', dest="cooldown", default='60', type=int) #периодичность проверки БД - в секундах
    args = parser.parse_args()

    with open(str(args.settings_file)) as f:
        config = yaml.safe_load(f)

    Monitor = MonitorM(config=config)
    Monitor.monitor(args.cooldown)



if __name__ == '__main__':
    main()
