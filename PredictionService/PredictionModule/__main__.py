import argparse, yaml

import pika
import psycopg2
import joblib
import sys
from datetime import timedelta, datetime

import numpy as np
from sklearn.preprocessing import MinMaxScaler, StandardScaler, RobustScaler
import ipaddress
import pandas as pd

from ModelLoader import ModelLoader

sys.path.append('/home/control/Documents/PredictionService/')
# from Datatypes import Predictor_pb2
from Datatypes.deserialize import decode_predictor_protobuff, decode_countMsg_protobuff


class PredictorM:

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


    def consume_predict(self, ch, method, props, body):
        # Принимает на вход из сообщения pkey PackagePredictor, для которого надо спрогнозировать данные.
        predictor = decode_predictor_protobuff(body, props)
        if predictor is None:
            print('Warning: wrong predictor for consume_predict! Skipped body ', body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
 
        result, *values = self.predict_for(predictor)
        
        if result == 1:
            # кинуть сообщение в рэббит - что надо создать для предиктора модель и после вызвать прогнозирование еще раз
            print(f'Info: consume_predict: result={result} for predictor={predictor}')
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.basic_publish( exchange='prediction_requests', routing_key='create', 
                        properties=pika.BasicProperties(headers={"proto":True, "then_predict":True}), 
                        body=body)
            return
        elif result == 2:
            print(f'Info: consume_predict: result={result} for predictor={predictor}')
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.basic_publish( exchange='prediction_requests', routing_key='fit', 
                        properties=pika.BasicProperties(headers={"proto":True, "then_predict":True}), 
                        body=body)
            return
        elif result == 0:
            print(f'Info: consume_predict: result={result} for predictor={predictor}')
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return


        vals = values[0]
        new_log_flag_ser_num = values[1]
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cursor:
                # добавляет спрогнозированные значения в таблицу
                try:
                    cursor.execute(f'''INSERT INTO Predicted_Package(ip_src, ip_dst, port_type, port_num, predicted_time, 
                                            predicted_time_diff, log_flag,
                                            predicted_by_pType_ip_src, predicted_by_pType_ip_dst, predicted_by_pType_port_group_id,  log_flag_series_num
                                        ) values {vals}
                                ''')
                except psycopg2.errors.UniqueViolation as e:
                    print(f"@@Error: got error. Tried to insert packets with already existing values. values={values} error: {e}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                ch.basic_ack(delivery_tag=method.delivery_tag)
                conn.commit()

                # Увеличичвает лог флаг в predictor таблице
                cursor.execute(f'''UPDATE Package_Predictor SET current_log_flag_series_num={new_log_flag_ser_num} 
                                WHERE pType_ip_src='{predictor.src}' and pType_ip_dst='{predictor.dst}' 
                                    and pType_port_group_id='{predictor.port_group_id}' and specific_ip_dst=inet'{predictor.specific_ip_dst}'
                                    ''') 
                            

    def consume_create(self, ch, method, props, body):
        # Принимает на вход из сообщения pkey PackagePredictor, для которого надо создать и обучить модель
        predictor = decode_predictor_protobuff(body, props)
        if predictor is None:
            print('Warning: wrong predictor for consume_create! Skipped body ', body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        res = self.create_model_for(predictor)
        if res is None:
            print(f"Error: failed to study model {predictor}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        elif not res:
            # print(f"@@Warning: model accuracy is too small for {predictor}. Will not use for predictions")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if props.headers is not None and props.headers.get("then_predict", False):
             ch.basic_publish( exchange='prediction_requests', routing_key='predict', 
                            properties=pika.BasicProperties(headers={"proto":True}), 
                            body=body)

        ch.basic_ack(delivery_tag=method.delivery_tag)


    def consume_fit(self, ch, method, props, body):
        # Принимает на вход из сообщения pkey PackagePredictor, для которого надо переобучить модель
        predictor = decode_predictor_protobuff(body, props)
        if predictor is None:
            print('Warning: wrong predictor for consume_fit! Skipped body ', body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        then_pred = props.headers.get("then_predict", False) if props.headers is not None else False 

        res = self.fit_model(predictor)
        if res is None:
            # если переобучение модели вышло с ошибкой
            print(f"Error: failed to re-study model {predictor}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        elif not res:
            # если модель не была найдена для переобучения - создать новую
            print(f"Warning: model not found will be created for predictor {predictor}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.basic_publish( exchange='prediction_requests', routing_key='create', 
                        properties=pika.BasicProperties(headers={"proto":True, "then_predict": then_pred}), 
                        body=body)
            return

        ch.basic_ack(delivery_tag=method.delivery_tag)


    def consume_count(self, ch, method, props, body):
        # Принимает на вход CountMsg
        msg = decode_countMsg_protobuff(body, props)
        if msg is None:
            print('Warning: wrong msg for consume_count! Skipped body: ', body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cur:
                # найти в Predicted_Package все пакеты старше того лог флага, что пришел + пакеты должны быть предсказаны одним предиктором (у них должен predicted_by совпадать)
                pred_where_key = f"pType_ip_src='{msg.predictor_src}' and pType_ip_dst='{msg.predictor_dst}' and pType_port_group_id='{msg.predictor_port_group_id}' and specific_ip_dst='{msg.predictor_specific_ip_dst}'"

                pred_pack_where_key = f"predicted_by_pType_ip_src='{msg.predictor_src}' and predicted_by_pType_ip_dst='{msg.predictor_dst}' and predicted_by_pType_port_group_id='{msg.predictor_port_group_id}' and ip_dst='{msg.predictor_specific_ip_dst}'"
                # clean predicted packages table
                cur.execute(f'''DELETE FROM Predicted_Package 
                                    WHERE {pred_pack_where_key} and log_flag_series_num < {msg.log_flag_series_num} 
                                    RETURNING was_hited, log_flag_series_num, log_flag; 
                        ''')
                packages = cur.fetchall()
                if packages is None or not packages or packages[0][0] is None:
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                # считаем точность
                cur.execute(f'''SELECT accuracy, total_hits FROM Package_Predictor WHERE {pred_where_key}
                    ''')
                acc = cur.fetchall()
                acc = acc[0]

                current_acc = acc[0] if acc[0] is not None else 0
                total_hits = acc[1] if acc[0] is not None else 0
                hit = 0
                total = 0
                for p in packages:
                    was_hited = p[0]
                    if was_hited:
                        hit += 1
                    total += 1
                if current_acc != 0:
                    new_acc = (total_hits + hit) / (int(total_hits/current_acc*100)+total) * 100
                else:
                    # если точность была 0 % то мы не можем через total_hits узнать, сколько было total всего. Поэтому рассчитываем точность, опираясь только на текущие данные
                    # TODO а можно ли это как-то поправить?
                    new_acc = hit / total * 100

                #задать новую точность и сменить лог флаг предиктору
                cur.execute(f'''UPDATE Package_Predictor SET accuracy='{new_acc}', counted_log_flag_series_num={msg.log_flag_series_num}, counted_log_flag={msg.log_flag}, total_hits={total_hits + hit}
                             WHERE {pred_where_key}
                    ''') 

                #если точность не удовлетворяет условиям - отправить запрос на переобучение модели
                accuracy_deviation = self.get_accuracy_deviation(cur)               
                if new_acc < (current_acc - accuracy_deviation):
                    ch.basic_publish( exchange='prediction_requests', routing_key='fit', 
                                properties=pika.BasicProperties(headers={"proto":True}), 
                                body=body)


        ch.basic_ack(delivery_tag=method.delivery_tag)



    def consume_clean(self, ch, method, props, body): 
        print('заготовка под очистку всех ненужных данных из БД по запросу ')
        ch.basic_ack(delivery_tag=method.delivery_tag)



    def get_pck_num(self, cursor, predictor_message):
        # считаем, сколько пакетов нужного типа есть в БД и сколько было раньше
        package_type_pkey = f"pType_ip_src='{predictor_message.src}' and pType_ip_dst='{predictor_message.dst}' and pType_port_group_id='{predictor_message.port_group_id}' "
        cursor.execute(f''' with t as (select * FROM Package_header_Package_Type 
                                WHERE {package_type_pkey} and ip_dst=inet'{predictor_message.specific_ip_dst}'
                            )                             
                            select COUNT(*) FROM t LEFT JOIN Package p ON 
                            p.ip_dst=t.ip_dst and p.ip_src=t.ip_src and p.port_type=t.port_type and p.port_num=t.port_num
                ''')
        res = cursor.fetchall()
        pck_num = res[0][0] if res[0][0] is not None else 0
        return pck_num


    def form_packets_from_prediction(self, cur,  predicted_y, last_timestap, lf_series_num, ptype, ip_dst):
        # в результате прогноза получим записи ip_dst  predicted_tdiff. Чтобы записать это в таблицу не хватает ip_src и predicted_timestap. Их берем из датасета (unique_ip_src_list from dataset) - то есть для каждого уникального ip_src из таблицы формируем свой кортеж из предсказанных значений
        # берем все подходящие по фильтру айпишники, тк смысл фильтра именно в том, чтобы предсказывать только для определенных ip и портов
        find_pckt_query = f''' select * FROM Package_header_Package_Type 
                            where ip_dst=inet'{ip_dst}' and pType_ip_src='{ptype[0]}' and pType_port_group_id='{ptype[2]}'
                            
                        '''
                        # --LIMIT {max_dataset_size} 
        cur.execute(f'''with p as ({find_pckt_query}) select distinct on (p.ip_src) p.ip_src, p.port_type, p.port_num from p
                ''')
        unique_ip_src_list = cur.fetchall()

        const_vals = f"'{ptype[0]}','{ptype[1]}','{ptype[2]}', {lf_series_num+1}"
        vals = ""
        for src in unique_ip_src_list: 
            print(f'@@Info: Predicting for src = {src}')
            ip_src = src[0]
            port_type = src[1]
            port_num = src[2]
            ctime = last_timestap
            cur_flag = 0
            # для каждого ip src формируем серию предсказанных пакетов
            for tdiff in predicted_y:
                ctime += timedelta(seconds=float(tdiff))
                cur_flag += 1
                vals += f"('{ip_src}', '{ip_dst}', '{port_type}', {port_num}, '{ctime}', {tdiff}, {cur_flag}, {const_vals}), "

        # return vals for writing into DB = "('ip_src', 'ip_dst', 'port_type', port_num, 'predicted_time', predicted_time_diff, 'predicted_by_pType_ip_src', 'predicted_by_pType_ip_dst', 'predicted_by_pType_port_group_id', 'current_log_flag_series_num'), (), ()"
        return vals[:-2], lf_series_num+1


    def predict_for(self, predictor_message):
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cur:
                # get model and predictor data
                package_type_pkey = f""" pType_ip_src='{predictor_message.src}' 
                                and pType_ip_dst='{predictor_message.dst}' and pType_port_group_id='{predictor_message.port_group_id}' """

                cur.execute(f''' with p as (SELECT * FROM Package_Predictor WHERE {package_type_pkey} and specific_ip_dst=inet'{predictor_message.specific_ip_dst}')
                        select name as model_name, prediction_seq_size, model_file_url, scaler_file_url, model_type, seq_length, current_log_flag_series_num, min_accuracy, accuracy, max_dataset_size, current_dataset_size, validation_accuracy
                        from p JOIN Model m ON m.name=p.use_model
                        ''')
                pred = cur.fetchall()
                predictor = pred[0]

                if predictor[0] is None:
                    print(f"Error: there is no model for predictor {predictor_message}. Will use default model and learn model first")
                    return 1, None
                
                model_url = predictor[2]
                scaler_url = predictor[3]
                if model_url is None or scaler_url is None:
                    print(f"Error: there is no model_url or scaler_url found for predictor {predictor_message}. Will learn model first")
                    return 1, None

                min_accuracy = predictor[7]
                accuracy = predictor[8]
                max_size = predictor[9]
                cur_size = predictor[10]
                validation_accuracy = predictor[11]

                model_acc = accuracy if accuracy>0 else validation_accuracy #TODO перпроверить. верно ли для всех случаях такое условие использовать
                if model_acc < min_accuracy:
                    # print(f'@@Info: predict_for: accuracy < min_accuracy accuracy={accuracy}, min_accuracy={min_accuracy}')
                    # отправить запрос на дообучение модели, если появилось достаточно новых данных
                    size = self.get_pck_num(cur, predictor_message)

                    if size >= max_size:
                        # если данных более, чем достаточно - то можно обучать, дообучать
                        return 2, None

                    size_div = self.get_size_deviation(cur)
                    # если число записей в датасете меньше минимального значения - то вернуть фолз, тк модель не может быть эффективна обучена
                    if size > (cur_size + cur_size/100*size_div):
                        # но, если записей стало больше, чем раньше - попробовать опять дообучить модель
                        return 2, None
                    # иначе - данных недостаточно - ниче делать не надо
                    return 0, None

                model_type = predictor[4]
                prediction_seq_size = predictor[1]
                current_log_flag_series_num = predictor[6]
                seq_len = predictor[5]
                
                # получаем прошлые значения из БД
                raw_data = self.get_data(cur, package_type_pkey, predictor_message.specific_ip_dst, seq_len+1)
                if raw_data is None:
                    print('#Warning: No packets for dataset found. Predictor is ', predictor_message)
                    return 0, None

                # print(f'@@Info: predicting packets for predictor_message={predictor_message} ')
                last_timestap = raw_data[-1][4]

                # загружаем модель и скейлер
                print(f'@@Info: Try to load model for predictor_message={predictor_message} model_url={model_url} model_type={model_type} scaler_url={scaler_url}')
                model = ModelLoader.load(model_url, model_type)
                scaler = joblib.load(scaler_url)

                # прогнозируем следующие prediction_size значений
                res_y = self.predict_data(model, raw_data, scaler, n_in=seq_len, n_out=prediction_seq_size)
     
                prediction_vals, new_log_flag_ser_num = self.form_packets_from_prediction(cur, res_y, last_timestap, current_log_flag_series_num, [predictor_message.src, predictor_message.dst, predictor_message.port_group_id], predictor_message.specific_ip_dst)

                return -1, prediction_vals, new_log_flag_ser_num



    def predict_data(self, model, pred_data, scaler, n_in=1, n_out=1, n_y_outputs = 1):
        # pred_data = list of tuples 

        # создаем из pred_data pd.DataFrame
        data = self.data_to_dataset(pred_data)
        if data is None:
            return None

        dataset = data.values.astype('float32')
        dataset = scaler.fit_transform(dataset)

        # retrieve last observations for input data
        input_x = dataset

        # reshape into [1, n_in, n_y_outputs]
        input_x = input_x.reshape((1, len(input_x), n_y_outputs))

        # forecast 
        prediction = model.predict(input_x, scaler)
        # потому что при таком формате даты выдает лист листов
        prediction = prediction[0]
        # retyrn predicted y
        return prediction



    def data_to_dataset(self, raw_data):
        # ожидает данные в виде raw_data=list of tuples = [(ip_src, ip_dst, port_type, port_num, time), (), (), ]
        # прверащает их в [()] то есть в list(tuple(ip.src, ip_dst, timediff))
        # возвращает pandasDataframe для такой даты
        try:
            last_time = raw_data[0][4]
        except IndexError:
            print(f'!!Error: wrong data format for {raw_data}')
            return None

        # data_cols = ["ip_src", "ip_dst", "time_diff"]
        data_cols = ["time_diff"]
        data = []
        # data = [[int(ipaddress.IPv4Address(raw_data[0][0])), int(ipaddress.IPv4Address(raw_data[0][1])), None]]
        for row in raw_data[1:]:
            cur_time = row[4]

            time_diff = (cur_time - last_time).total_seconds()
            last_time = cur_time
            # data.append([int(ipaddress.IPv4Address(row[0])), int(ipaddress.IPv4Address(row[1])), time_diff] )
            data.append([time_diff] )

        # create Pandas Dataframe from data
        data = pd.DataFrame(data, columns=data_cols)
        # print('Created dataset ', data)
        return data


    # convert history into inputs and outputs - используется, чтобы сформировать датасет на котором можно будет обучить модель
    def to_supervised(self, data, n_input, n_out=1):
        X, y = list(), list()
        in_start = 0
        # step over the entire history one time step at a time
        for _ in range(len(data)):
            # define the end of the input sequence
            in_end = in_start + n_input
            out_end = in_end + n_out
            # ensure we have enough data for this instance
            if out_end <= len(data):
                x_input = data[in_start:in_end, 0]
                x_input = x_input.reshape((len(x_input), 1))
                X.append(x_input)
                y.append(data[in_end:out_end, 0])
            # move along one time step
            in_start += 1
        return np.array(X), np.array(y)

    '''shape данных подаавемых 
    на обучение [samples, timesteps, features]=[кол-во строк, prediction_seq_size, 1]
        samples = сколько строк подать на вход на обучение 
        timesteps = сколько строк предсказывать надо в дальнейшем = prediction_seq_size
        features = 1, тк предсказываем только одну переменную time_diff
    на предсказние [samples, timesteps, features]==[1, prediction_seq_size, 1]
        features = 1, тк предсказываем только одну переменную time_diff
        prediction_seq_size = timesteps = сколько следующих значений надо предсказывать
        samples = 1 - тк делаем одну серию предсказаний
    форма данных должна совпадать - могут только число строк для обучения и для предсказния отличаться

    подаем при обучении seq_len строк +1(которая в датасет не попадет) -тк именно на основе seq_len строк предсказываем результат, а еще одна доп строка нужна, чтобы просчитать, какой будет time diff для первой (из seq_len строк) строки'''

    def create_train_dataset(self, raw_data, scaler, seq_len=1, future_seq_len=1):
        # ожидает данные в виде raw_data=list of tuples = [(ip_src, ip_dst, port_type, port_num, time), (), (), ]
        # seq+future_seq_len должен быть < len(строчки в датасете)
        # return numpy.ndarray

        data = self.data_to_dataset(raw_data)
        
        if data is None:
            return None, None

        # print('Всего строчек в датасете = ', len(data.index))
        
        dataset = data.values.astype('float32')
        dataset = scaler.fit_transform(dataset)
        # print("Validation data after scaling = ", pd.DataFrame(dataset))

        # create dataset to frame as supervised learning
        dataset_x, dataset_y = self.to_supervised(dataset, seq_len, future_seq_len)
        # print(f"Dataset shape ={dataset.shape}, Dataset X shape ={dataset_x.shape}, Dataset Y shape ={dataset_y.shape} ")
        return dataset_x, dataset_y

    def validate_model(self, model, valid_data, scaler, seq_len=1, prediction_seq_size=1, validation_num = None, max_time_diff=0, time_make_way=0):

        if validation_num is None:
            validation_num = prediction_seq_size

        # проверяем точность модели на тестовых данных
        # предсказываем validation_num значений сериями по n_out значений
        # после каждого предсказания - сравниваем реальные данные с предсказанными - изменяем датасет и предсказываем еще раз
        
        total_pred_y = []
        frame_start = 0
        frame_end = seq_len

        total_pred_num = 0

        # предсказываем validation_num значений сериями по prediction_seq_size на основе seq_len значений (+ еще одно, чтобы посчитать seq_len[0] значение)
        while True:
            # предсказываем по аналогии с обычным предсказанием - берем предыдущие значения + еще одно, чтобы рассчитать time_diff для -(seq_len) значения
            history = valid_data[frame_start:frame_end+1]
            # print(f"history = {history}, frame_start = {frame_start}, frame_end={frame_end}, total_pred_num={total_pred_num}, penultimate_elem_num={validation_num + seq_len - 1}")
            
            pred_y = self.predict_data(model, history, scaler, n_in=seq_len, n_out=prediction_seq_size)
            # print("Предсказанные значения pred_y = ", pred_y) 

            total_pred_y = [*total_pred_y, *pred_y]
        
            # меняем данные 
            total_pred_num += prediction_seq_size

            if total_pred_num >= validation_num:
                # датасет при финальном разе - это все элементы кроме последнего
                frame_end = validation_num + seq_len - 1 #=penultimate_elem_num
                frame_start = frame_end - seq_len
                k = total_pred_num - validation_num
                if k > 0:
                    total_pred_y = total_pred_y[:-k] # если есть лишние значения то #сколько лишних значений предсказано столько убираем
                break

            frame_start += prediction_seq_size
            frame_end += prediction_seq_size
        
        # print("@@Info: Все предсказанные значения = ", total_pred_y)
        
        # считаем точность
        # получаем реальный y из датасета - получаем значения time и рассчитывает для них tdiff
        real_y = []
        valid_data = np.array(valid_data[seq_len:])[:,-1]
        last_time = valid_data[0]

        for cur_time in valid_data[1:]:
            time_diff = (cur_time - last_time).total_seconds()
            last_time = cur_time
            real_y.append(time_diff)
            
        # сравниваем реальный и предсказанный y
        # print("@@Info: Реальные значения y = ", real_y)

        hits, accuracy = model.count_error(real_y, total_pred_y, max_time_diff=max_time_diff, time_make_way=time_make_way)
        return accuracy, hits


    def create_model_for(self, predictor_message):
        # return None if error, True if can use model for prediction, False if models accuracy or used dataset is too small
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cur:
                package_type_pkey = f"pType_ip_src='{predictor_message.src}' and pType_ip_dst='{predictor_message.dst}' and pType_port_group_id='{predictor_message.port_group_id}' "
                predictor_pkey = package_type_pkey+f" and specific_ip_dst=inet'{predictor_message.specific_ip_dst}' "
                # get model and predictor data
                found_model_query = f''' with p as (SELECT * FROM Package_Predictor WHERE {predictor_pkey})
                        select model_type, name as model_name, repeat, batch_size, epochs, neurons_ammount, dropout_percent, loss_f, max_dataset_size, min_dataset_size, scaler_name, prediction_seq_size, seq_length, current_dataset_size, validation_accuracy, model_file_url, scaler_file_url
                        from p JOIN Model m ON m.name=p.use_model '''
                cur.execute(found_model_query)
                predictor = cur.fetchall()
                predictor = predictor[0]

                used_model=True

                if predictor[0] is None:
                    print(f"Error: there is no model found for predictor {predictor_message}. Will use the default model")
                    cur.execute(''' SELECT select_text_setting('default_model') ''')
                    def_model = cur.fetchall()
                    def_model = def_model[0][0]
                    if def_model is None:
                        print("Error: no default model found in settings!")
                        return None

                    # TODO def_model может быть в настройках, но не быть в Model - в initalizer надо бы проверять, что указанная default-model действительно есть в настройках
                    cur.execute(f''' UPDATE Package_Predictor set use_model='{def_model}' WHERE {predictor_pkey} ''')
                    conn.commit()
                    cur.execute(found_model_query)
                    predictor = cur.fetchall()
                    predictor = predictor[0]
                    used_model = False
                    
                # создаем модель и используем ее для обучения
                model_type = predictor[0]
                repeat = predictor[2]
                batch_size = predictor[3]
                epochs = predictor[4]
                neuro_ammount = predictor[5]
                dropout_percent = predictor[6] 
                loss_f = predictor[7]
                max_dataset_size = predictor[8]
                min_dataset_size = predictor[9]
                scaler_name = predictor[10]
                prediction_len = predictor[11] #сколько следующих значений должна предсказывать модель
                seq_len = predictor[12] #сколько прошлых значений использовать для прогнозирования следующего
                cur_size = predictor[13]

                val_acc = predictor[14]
                model_url = predictor[15]
                scaler_url = predictor[16]

                if used_model and val_acc is not None and model_url is not None and scaler_url is not None:
                    # если модель уже обучали
                    # не обучаем ее еще раз
                    print('@@Info: Model for predictor_message={predictor_message} already was created')
                    return False

                # вводим эту проверку, чтобы не выполнять попусту get_data - КАЖДЫЙ раз
                # обучать только - если появилось достаточно новых данных
                size = self.get_pck_num(cur, predictor_message)
                size_div = self.get_size_deviation(cur)
                # если число записей в датасете меньше минимального значения - то вернуть фолз, тк модель не может быть эффективна обучена
                if size <= ( cur_size/100*(1+size_div) ):
                    # но, если записей стало больше, чем раньше - попробовать опять дообучить модель
                    # print(f"@@Info: size <= ( cur_size/100*(1+size_div) )=True size={size} cur_size={cur_size} size_div={size_div}")
                    return False

                # формирует датасет из max_dataset_size последних пакетов
                raw_data = self.get_data(cur, package_type_pkey, predictor_message.specific_ip_dst, max_dataset_size) 
                
                if raw_data is None:
                    print(f'@@Warning: No packets for dataset found. Predictor is {predictor_message}')
                    return None

                pck_num = size #len(raw_data)
                cur.execute(f''' UPDATE Package_Predictor set current_dataset_size='{pck_num}' WHERE {predictor_pkey} ''')
                
                if pck_num < min_dataset_size:
                    # если число записей в датасете меньше минимального значения - то вернуть фолз, тк модель не может быть эффективна обучена
                    # print(f'@@Info: pck_num < min_dataset_size pck_num={pck_num} min_dataset_size={min_dataset_size}')
                    return False
                
                print(f"@@Info: learn model for predictor_message={predictor_message}")
                # обучаем модель
                scaler = getattr(sys.modules[__name__], scaler_name)()

                train_x, train_y = self.create_train_dataset(raw_data, scaler, seq_len=seq_len, future_seq_len=prediction_len)

                if train_x is None or train_y is None:
                    return None


                data_shape = (train_x.shape[1], train_x.shape[2]) #n_timesteps, n_features
                n_outputs = (train_y.shape[1]) #n_outputs == prediction_len
                
                model = ModelLoader.create_model( model_type, inp_shape=data_shape, n_outputs = n_outputs, loss_f=loss_f, neuro_ammount=neuro_ammount, dropout_percent=dropout_percent, epochs=epochs, batch_size=batch_size, repeat=repeat )

                # обучается на датасете
                time_of_train = model.train(train_x, train_y)


                # считаем предварительную точность данных для valid_dataset
                validation_perc, max_time_diff, time_make_way = self.get_evaluate_params(cur)

                # на последних validation_perc % датасета - проверим точность модели
                validation_num = int(pck_num / 100 * validation_perc)

                # считаем предварительную точность данных для valid_dataset
                # берем validation_num значения + предыдущие значения + еще одно, чтобы рассчитать time_diff для первого предыдущего
                pred_raw_data = raw_data[-(validation_num+seq_len+1):] 

                validation_accuracy, hits = self.validate_model(model, pred_raw_data, scaler, seq_len=seq_len, prediction_seq_size=prediction_len, validation_num = validation_num, max_time_diff=max_time_diff, time_make_way=time_make_way)
        
                # print(f"Validation: hits={hits}, validation_accuracy ={validation_accuracy}")
                
                #  записать модель и значение точности в БД
                
                # сохранить модель
                # взять из БД scaler_files_dir и model_files_dir
                model_files_dir = self.get_model_dir(cur)
                scaler_files_dir = self.get_scaler_dir(cur)
                
                print(f'model.generate_model_filename() = {model.generate_model_filename()}')
                print(f'type(scaler).__name__ = {type(scaler).__name__}')
                print(f'scaler_files_dir = {scaler_files_dir}')

                scaler_filename = scaler_files_dir + type(scaler).__name__  + model.generate_model_filename(dop_file_name=f"{predictor_message.src}-{predictor_message.dst}-{predictor_message.port_group_id}-{predictor_message.specific_ip_dst}")
                joblib.dump(scaler, scaler_filename)

                model_saved_file = model.save(model_files_dir, dop_file_name=f"{predictor_message.src}-{predictor_message.dst}-{predictor_message.port_group_id}-{predictor_message.specific_ip_dst}")
                model_saved_file = model_saved_file[0]
                
                # записать новую модель name в Package_Predictor use_model model_file_url scaler_file_url (закомитить сразу это изменение)
                cur.execute(f''' UPDATE Package_Predictor set time_of_train='{time_of_train}',  model_file_url='{model_saved_file}', scaler_file_url='{scaler_filename}', validation_accuracy='{validation_accuracy}' WHERE {predictor_pkey} ''')

                conn.commit()

                cur.execute(f''' SELECT select_int_setting('min_accuracy') ''')
                min_accuracy = cur.fetchall()
                min_accuracy = min_accuracy[0]
                if min_accuracy is None or validation_accuracy >= int(min_accuracy[0]):
                    # можно использовать модель
                    print(f"@@Info: Model accuracy is great! min_accuracy={min_accuracy[0]} validation_accuracy={validation_accuracy}")
                    return True
                else:
                    print(f"@@Info: Model accuracy is bad and low. min_accuracy={min_accuracy[0]} validation_accuracy={validation_accuracy}")
                    return False
  
        return None


    def get_data(self, cursor, package_type_pkey, ip_dst, max_dataset_size):
        cursor.execute(f''' with t as (select * FROM Package_header_Package_Type 
                                WHERE {package_type_pkey} and ip_dst=inet'{ip_dst}'
                            ) 
                        select p.ip_src, p.ip_dst, p.port_type, p.port_num, time 
                        from t LEFT JOIN Package p ON 
                            p.ip_dst=t.ip_dst and p.ip_src=t.ip_src and p.port_type=t.port_type and p.port_num=t.port_num
                        ORDER BY time DESC LIMIT {max_dataset_size} ''')
        raw_data = cursor.fetchall()

        if raw_data[0][0] is None:
            return None
        # тк пакеты пришли в убывающем порядке - переворачиваем список в обратную сторону
        raw_data.reverse()
        return raw_data


    def fit_model(self, predictor_message):
        print(f'@@Info: try to fit for {predictor_message} ')
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cur:
                # get model and predictor info
                package_type_pkey = f""" pType_ip_src='{predictor_message.src}' 
                                and pType_ip_dst='{predictor_message.dst}' and pType_port_group_id='{predictor_message.port_group_id}' """

                cur.execute(f''' with p as (SELECT * FROM Package_Predictor WHERE {package_type_pkey} and specific_ip_dst=inet'{predictor_message.specific_ip_dst}')
                        select name as model_name, model_file_url, scaler_file_url, seq_length, prediction_seq_size, model_type, loss_f,max_dataset_size, epochs, repeat, batch_size, neurons_ammount, dropout_percent, min_dataset_size, accuracy
                        from p JOIN Model m ON m.name=p.use_model
                        ''')
                pred = cur.fetchall()
                predictor = pred[0]

                if predictor[0] is None:
                    print(f"Error: there is no model for predictor {predictor_message}. Will learn a new model")
                    return False
                
                # загружаем скейлер
                model_file_url = pred[1]
                scaler_file_url = pred[2]
                scaler = joblib.load(scaler_file_url)

                seq_len = pred[3]
                prediction_len = pred[4]
                model_type = pred[5]
                max_dataset_size = pred[7]
                loss_f = pred[6]
                neuro_ammount = pred[11]
                batch_size = pred[10]
                repeat = pred[9]
                epochs = pred[8]
                dropout_percent = pred[12]
                min_dataset_size = pred[13]
                curr_accuracy = pred[14]

                # загружаем датасет
                raw_data = self.get_data(cur, package_type_pkey, predictor_message.specific_ip_dst, max_dataset_size) 
                pck_num = len(raw_data)
                cur.execute(f''' UPDATE Package_Predictor set current_dataset_size='{pck_num}' WHERE {package_type_pkey} and specific_ip_dst=inet'{predictor_message.specific_ip_dst}' ''')
                if pck_num < min_dataset_size:
                    # если число записей в датасете меньше минимального значения - то вернуть фолз, тк модель не может быть эффективна обучена
                    return None

                train_x, train_y = self.create_train_dataset(raw_data, scaler, seq_len=seq_len, future_seq_len=prediction_len)

                if train_x is None or train_y is None:
                    return None

                data_shape = (train_x.shape[1], train_x.shape[2]) #n_timesteps, n_features
                n_outputs = (train_y.shape[1]) #n_outputs == prediction_len
                
                # загружаем модель
                model = ModelLoader.load(model_file_url, model_type)
                # задаем self аргументы модели, чтобы можно было ее сохрнаить с норм именем
                model.set_params(inp_shape=data_shape, n_outputs = n_outputs, loss_f=loss_f, neuro_ammount=neuro_ammount, dropout_percent=dropout_percent, epochs=epochs, batch_size=batch_size, repeat=repeat)
                

                # обучается на датасете
                time_of_train = model.train(train_x, train_y)


                # считаем предварительную точность данных для valid_dataset
                validation_perc, max_time_diff, time_make_way = self.get_evaluate_params(cur)
                # на последних validation_perc % датасета - проверим точность модели
                validation_num = int(pck_num / 100 * validation_perc)

                # считаем предварительную точность данных для valid_dataset
                # берем validation_num значения + предыдущие значения + еще одно, чтобы рассчитать time_diff для первого предыдущего
                pred_raw_data = raw_data[-(validation_num+seq_len+1):] 
                validation_accuracy, hits = self.validate_model(model, pred_raw_data, scaler, seq_len=seq_len, prediction_seq_size=prediction_len, validation_num = validation_num, max_time_diff=max_time_diff, time_make_way=time_make_way)


                # if curr_validation_accuracy < validation_accuracy:
                #     то сделать то, что ниже

                # обновить точность для validation_accuracy и time_of_train + пересохранить модель
                model_files_dir = self.get_model_dir(cur)
                model_saved_file = model.save(model_files_dir, dop_file_name=f"{predictor_message.src}-{predictor_message.dst}-{predictor_message.port_group_id}-{predictor_message.specific_ip_dst}")
                model_saved_file = model_saved_file[0]
                
                # записать новую модель name в Package_Predictor use_model model_file_url scaler_file_url (закомитить сразу это изменение)
                cur.execute(f''' UPDATE Package_Predictor set time_of_train='{time_of_train}',  model_file_url='{model_saved_file}', validation_accuracy='{validation_accuracy}' WHERE {package_type_pkey} and specific_ip_dst=inet'{predictor_message.specific_ip_dst}' 
                ''')
                return True
                
        return None


    def get_evaluate_params(self, cur):
        cur.execute(''' SELECT select_int_setting('validation_data_ammount') ''')
        validation_perc = cur.fetchall()
        validation_perc = validation_perc[0][0]
        if validation_perc is None:
            print("Info: no validation_data_ammount in set in Settings")
            validation_perc = 0
        else:
            validation_perc = int(validation_perc)

        cur.execute(''' SELECT select_int_setting('delay') ''')
        max_time_diff = cur.fetchall()
        max_time_diff = max_time_diff[0][0]
        if max_time_diff is None:
            print("Info: no delay in set in Settings")
            max_time_diff = 0
        else:
            max_time_diff = int(max_time_diff)

        cur.execute(''' SELECT select_int_setting('time_make_way') ''')
        time_make_way = cur.fetchall()
        time_make_way = time_make_way[0][0]
        if time_make_way is None:
            print("Info: no time_make_way in set in Settings")
            time_make_way = 0
        else:
            time_make_way = int(time_make_way)

        return validation_perc, max_time_diff, time_make_way


    def get_model_dir(self, cur):
        cur.execute(''' SELECT select_text_setting('model_files_dir') ''')
        model_files_dir = cur.fetchall()
        model_files_dir = model_files_dir[0][0]
        model_files_dir = model_files_dir if model_files_dir is not None else "./"
        return model_files_dir

    
    def get_scaler_dir(self, cur):
        cur.execute(''' SELECT select_text_setting('scaler_files_dir') ''')
        scaler_files_dir = cur.fetchall()
        scaler_files_dir = scaler_files_dir[0][0]
        scaler_files_dir = scaler_files_dir if scaler_files_dir is not None else "./"
        return scaler_files_dir


    def get_accuracy_deviation(self, cur):
        cur.execute(''' SELECT select_int_setting('accuracy_deviation') ''')
        acc_dev = cur.fetchall()
        acc_dev = acc_dev[0][0]
        acc_dev = acc_dev if acc_dev is not None else 0
        return acc_dev


    def get_size_deviation(self, cur):
        cur.execute(''' SELECT select_int_setting('size_deviation') ''')
        res = cur.fetchall()
        res = res[0][0]
        res = res if res is not None else 0
        return res


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-sf', '--settings_file', dest="settings_file", default='settings.yml', type=str) #путь до файла с настройками
    args = parser.parse_args()

    with open(str(args.settings_file)) as f:
        config = yaml.safe_load(f)

    Predictor = PredictorM(config=config)
    with pika.BlockingConnection(pika.ConnectionParameters(**Predictor.rbmq_conn_dict)) as connection:
        channel = connection.channel()
        channel.basic_consume(queue='predict_pckts_reqs', on_message_callback=Predictor.consume_predict)
        channel.basic_consume(queue='create_model_reqs', on_message_callback=Predictor.consume_create)
        channel.basic_consume(queue='fit_model_reqs', on_message_callback=Predictor.consume_fit)
        channel.basic_consume(queue='count_error_reqs', on_message_callback=Predictor.consume_count)
        channel.basic_consume(queue='clean_reqs', on_message_callback=Predictor.consume_clean)

        print(" [x] Awaiting requests")
        channel.start_consuming()



if __name__ == '__main__':
    main()


