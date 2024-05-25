# запускается один раз для инициализации БД и брокера сообщений + для миграций
# python3 Initializer [options]

import argparse
import yaml
import pika

import psycopg2
from Migrations.DB.Init import InitDB
from Migrations.Rabbit.Init import InitRabbit
from Migrations.DB.Migration1 import Migration1 as Migr1

from Migrations.MigrationBase import select

import ipaddress


class Initializer:

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

        self.migrations = [] 
        # Migr1()
        self.config = config

        
    def initialize(self):
        migrations = [InitDB(), InitRabbit()]
        for migr in migrations:
            migration_is_applied = select(self.pg_connection_dict, f"select exists (select true from _migrations where name='{migr.__class__.__name__}');")
            # migration_is_applied is None - для того, чтобы, проверять (не обращаясь еще раз к бд) есть ли _migrations, если нет, значит миграцию точно можно применить
            if migration_is_applied is None or not migration_is_applied[0][0]:
                res = migr.apply(pg_con = self.pg_connection_dict, rbmq_con = self.rbmq_conn_dict)
                if not res:
                    print(f"Migration {migr.__class__.__name__} was not succesfully applied")


    def update(self):
        for migration in self.migrations:
            migration_is_applied = select(self.pg_connection_dict, f"select exists (select true from _migrations where name='{migration.__class__.__name__}');")
            if not migration_is_applied[0][0]:
                res = migration.apply(pg_con = self.pg_connection_dict, rbmq_con = self.rbmq_conn_dict)
                if not res:
                    print(f"Migration {migration.__class__.__name__} was not succesfully applied")


    def update_settings(self):
        with psycopg2.connect(**self.pg_connection_dict) as conn:
            with conn.cursor() as cursor:
                print('Updating general settings..')
                self.update_global(cursor)
                print('Updating models info..')
                self.update_model_info(cursor)
                print('Updating ports info..')
                self.update_port_info(cursor)
                print('Updating subnets info..')
                self.update_ipGroups_info(cursor)
                print('Updating packageTypes info..')
                self.update_packageType_info(cursor)
                print('Success!')


    def update_global(self, cur):
        # delete old settings
        settings = self.config['general_settings'].keys()
        cur.execute(f'''delete from Settings where param_name not in ('{"','".join(settings)}')''')
        for s in settings:
            sval = self.config['general_settings'].get(s)
            if isinstance(sval, int):
                sett_keys = "param_name, param_type, value_int"
                sett_vals = f"'{s}', 'int', '{sval}' "
            else:
                sett_keys = "param_name, param_type, value_txt"
                sett_vals = f"'{s}', 'string', '{sval}' "

            cur.execute(f"insert into Settings ({sett_keys}) values ({sett_vals}) ON CONFLICT (param_name) DO UPDATE SET param_type=EXCLUDED.param_type, value_txt=EXCLUDED.value_txt, value_int=EXCLUDED.value_int")


    def update_model_info(self, cur):
        #TODO сделать валидацию по model_type и loss_f, тк допускается жесткий перечень значений
        # TODO сделать значения по умолчанию, если в конфигах не задано значение
        #  сделать валидацию данных - если не введены все параметры (как в update_packageType_info)
        # delete old models
        models = self.config['models'].keys()
        cur.execute(f'''delete from Model where name not in ('{"','".join(models)}')''')
        
        for name, model in self.config['models'].items():
            # если модель была изменена (только важные характеристики, влияющие на обучение) - сбрасываем ее характеристики для всех PackPredictor-ов
            # TODO если изменить имя моедли, но не менять ее характеристики, то тоже сбросятся показатели, хотя не должны. Нужно либо добавить третье условие (как второе, но без name='{name}) либо что-то еще
            if self.exists('Model', where=f"name='{name}'") and not self.exists('Model', where=f"name='{name}' and model_type='{model['model_type']}' and loss_f='{model['loss_f']}' and seq_length={model['seq_length']} and epochs={model['epochs']} and repeat={model['repeat']} and batch_size={model['batch_size']} and neurons_ammount={model['neurons_ammount']} and dropout_percent={model['dropout_percent']}"):
                # если модель существует и ее основные характеристики менялись
                cur.execute(f'''UPDATE Package_Predictor set accuracy=0, validation_accuracy=null, time_of_train=null, model_file_url=null, scaler_file_url=null, total_hits=0
                                where use_model='{name}'; ''')

            keys = "name, model_type, loss_f, seq_length, epochs, repeat, batch_size, neurons_ammount, dropout_percent"
            vals = f"'{name}', '{model['model_type']}', '{model['loss_f']}', '{model['seq_length']}', '{model['epochs']}', '{model['repeat']}', '{model['batch_size']}', '{model['neurons_ammount']}', '{model['dropout_percent']}'"
            excluded_vals = "model_type=EXCLUDED.model_type, loss_f=EXCLUDED.loss_f, seq_length=EXCLUDED.seq_length, epochs=EXCLUDED.epochs, repeat=EXCLUDED.repeat, batch_size=EXCLUDED.batch_size, neurons_ammount=EXCLUDED.neurons_ammount, dropout_percent=EXCLUDED.dropout_percent"

            if model.get('prediction_size') is not None:
                keys+=", prediction_seq_size"
                vals+=f", '{model['prediction_size']}'"
                excluded_vals+= ", prediction_seq_size=EXCLUDED.prediction_seq_size" 
            
            if model.get('max_dataset_size') is not None:
                keys+=", max_dataset_size"
                vals+=f", '{model['max_dataset_size']}'"
                excluded_vals+= ", max_dataset_size=EXCLUDED.max_dataset_size" 

            if model.get('validation_data_ammount') is not None:
                keys+=", validation_data_ammount"
                vals+=f", '{model['validation_data_ammount']}'"
                excluded_vals+= ", validation_data_ammount=EXCLUDED.validation_data_ammount" 
            
            if model.get('min_accuracy') is not None:
                keys+=", min_accuracy"
                vals+=f", '{model['min_accuracy']}'"
                excluded_vals+= ", min_accuracy=EXCLUDED.min_accuracy" 

            if model.get('min_dataset_size') is not None:
                keys+=", min_dataset_size"
                vals+=f", '{model['min_dataset_size']}'"
                excluded_vals+= ", min_dataset_size=EXCLUDED.min_dataset_size"

            cur.execute(f'''insert into Model ({keys}) values ({vals}) ON CONFLICT(name) DO UPDATE SET {excluded_vals}
                ''')


    def update_port_info(self, cur):
        # delete all port_groups that are not listed in settings.yaml 
        active_groups = "','".join(self.config['port_group'].keys()) + "', 'any-tcp', 'any-udp', 'any"
        cur.execute(f"delete from Port_group where group_name not in ('{active_groups}')")

        def insert_p(ptype, port_list, pg_name):
            p_values = ', '.join(f"({n}, '{ptype}')" for n in port_list)
            cur.execute(f"insert into Port (port_num, port_type) values {p_values} ON CONFLICT DO NOTHING")
            ppg_values = ', '.join(f"('{pg_name}', {n}, '{ptype}')" for n in port_list)
            cur.execute(f"insert into Port_Port_group (group_name, port_num, port_type) values {ppg_values} ON CONFLICT DO NOTHING")

        for pg_name, pg in self.config["port_group"].items():
            tcp_ports = pg.get("tcp")
            udp_ports = pg.get("udp")
            
             # and add new one from settings.yaml
            cur.execute(f"insert into Port_group (group_name) values ('{pg_name}') ON CONFLICT DO NOTHING")
            
            if tcp_ports is not None:
                if udp_ports is not None:
                    print(f"WARNING for group {pg_name}: only one port type can be mentioned on group")
                    continue
                else:
                    insert_p('tcp', tcp_ports, pg_name)
                    insert_p('tcp', tcp_ports, 'any')
                    insert_p('tcp', tcp_ports, 'any-tcp')
            else:
                if udp_ports is not None:
                    insert_p('udp', udp_ports, pg_name)
                    insert_p('udp', udp_ports, 'any')
                    insert_p('udp', udp_ports, 'any-udp')
                else:
                    print(f"WARNING for group {pg_name}: no ports mentioned")


    def update_packageType_info(self, cur):
        # delete all port_groups that are not listed in settings.yaml 
        active_types = []

        for pck_type in self.config['package_type']:
            
            # валидация введенных данных
            if pck_type is None or pck_type.get('ip_dst') is None or pck_type.get('is_monitored') is None:
                print(f"Warning for packetType {pck_type}: packet is not valid. Some parameters are blank or dont exists. \n PacketType was skipped")
                continue
            
            if pck_type.get('ip_src') is not None:
                ip_src = self.config['subnet_group'][pck_type['ip_src']]['filter_sting']
            else:
                ip_src = '0.0.0.0/0'

            if pck_type.get('port_group_id') is not None:
                port_group_id = pck_type.get('port_group_id')
            else:
                port_group_id = 'any'

            ip_dst = self.config['subnet_group'][pck_type['ip_dst']]['filter_sting']
            pkey = f"'{ip_src}', '{ip_dst}', '{port_group_id}'"
            active_types.append(pkey)

            # add new one or update existing
            # если такая запись уже есть - обновляем только те поля, которые не primary key - ведь их значение могло изменится
            cur.execute(f"insert into Package_Type (ip_src, ip_dst, port_group_id, is_monitored) values ('{ip_src}', '{ip_dst}', '{port_group_id}', {pck_type['is_monitored']}) ON CONFLICT (ip_src, ip_dst, port_group_id) DO UPDATE SET is_monitored=EXCLUDED.is_monitored")

            # и добавляем пакеты для этого типа пакета
            cur.execute(f'''
                with src as (
                    (SELECT net_address as src_net_address, group_filter_string
                    FROM Ip_Subnet_ip_group as subn
                    where subn.group_filter_string in ('{ip_src}', 'any') )
                ), dst as (
                    select net_address as dst_net_address, group_filter_string
                    FROM Ip_Subnet_ip_group as subn2
                    where subn2.group_filter_string = '{ip_dst}'
                ),  ips as (
                    select src_net_address, dst_net_address, src.group_filter_string as src_filter_string, dst.group_filter_string as dst_filter_string  
                    from src LEFT JOIN dst on true
                ),	ports as (
                    select port_type, port_num, group_name as port_group_name
                    FROM Port_Port_group as pgr
                    where pgr.group_name in ('{port_group_id}', 'any', 'any-tcp', 'any-udp')
                ), t as (select * from ips CROSS JOIN ports)

                select ip_src, ip_dst, p.port_type, p.port_num
                FROM Package_header as p
                INNER JOIN t ON ( p.ip_src <<= t.src_net_address and p.ip_dst <<= t.dst_net_address
                and p.port_type = t.port_type and p.port_num = t.port_num
                            )
            ''')
            appropirate_packets = cur.fetchall()
            for p in appropirate_packets:
                cur.execute(f"insert into Package_header_Package_Type (pType_ip_src, pType_ip_dst, pType_port_group_id, ip_src, ip_dst, port_type, port_num) \
                      values ('{ip_src}', '{ip_dst}', '{port_group_id}', \
                      '{p[0]}', '{p[1]}', '{p[2]}', '{p[3]}') ON CONFLICT DO NOTHING")


            # и добавляем packagePredictor для этого типа пакетов для каждого адреса из ip_dst
            # удалять предикторы не надо, тк они зависят от packageType и удалятся сами, если packageType под них нету
            print(f'Updating packagePredictor for , {ip_src}, {ip_dst}, {port_group_id}')
            cur.execute(f"SELECT distinct net_address FROM Ip_Subnet_ip_group where group_filter_string='{ip_dst}'")
            all_ip_dst_subn = cur.fetchall()

            # при необходимости добавляем модель от пользователя, иначе используем деволтную
            key = "pType_ip_src, pType_ip_dst, pType_port_group_id, specific_ip_dst"
            vals = f"'{ip_src}', '{ip_dst}', '{port_group_id}'"
            if pck_type.get('use_model') is not None:
                key = "use_model, "+key
                use_model = pck_type.get('use_model')
                vals = f"'{use_model}', "+vals
            else:
                use_model = self.config['general_settings']['default_model']

            if pck_type.get('default_scaler') is not None:
                key = "scaler_name, "+key
                scaler_name = pck_type.get('default_scaler')
                vals = f"'{scaler_name}', "+vals
            else:
                scaler_name = self.config['general_settings']['default_scaler']

            on_conflict_vals = f"use_model='{use_model}', scaler_name='{scaler_name}'"

            # проверяем, что use_model изменился на иной. Делаем в коде, тк через триггер все оооочень медленно начинает работать
            cur.execute(f"SELECT use_model FROM Package_Predictor WHERE pType_ip_src = '{ip_src}' and pType_ip_dst = '{ip_dst}' and pType_port_group_id = '{port_group_id}'")
            found_ip_dst = cur.fetchall()
            old_use_model = found_ip_dst[0][0] if found_ip_dst else None
            if old_use_model is not None and use_model != old_use_model:
                key = "accuracy, total_hits, validation_accuracy, time_of_train, model_file_url=null, scaler_file_url, " + key
                vals = "0, 0, null, null, null, null, " + vals
                on_conflict_vals+= ", accuracy=0, total_hits=0, validation_accuracy=null, time_of_train=null, model_file_url=null, scaler_file_url=null"

            full_vals = ""
            for dst_subn in all_ip_dst_subn:
                dst_subn = dst_subn[0]
                # print('#Updating dst_subn=', dst_subn)
                # генерируем все возможные ip адреса для подсети
                for dst_ip in ipaddress.IPv4Network(dst_subn):
                    full_vals += f"({vals}, '{str(dst_ip)}'),"
            full_vals = full_vals[:-1]
            cur.execute(f'''insert into Package_Predictor ({key}) values {full_vals}
                        ON CONFLICT ON CONSTRAINT Package_Predictor_Pkey DO UPDATE SET {on_conflict_vals} ''')

        active_types_str = '),('.join(active_types)
        # delete all packageTypes that are not in config
        print('Deleting old Package_Types...')
        cur.execute(f"delete from Package_Type where (ip_src, ip_dst, port_group_id) not in (({active_types_str}))")
        
 
    def generate_subnet_list(self, filter_sting):
        mask = filter_sting.split("/")
        filt = mask[0]
        try:
            mask = '/'+mask[1]
        except IndexError as er:
            return [filter_sting]

        octets = filt.split(".")

        def cross_join(list1, list2, end='', delimeter=''):
            # how it works 
            # [a,b] [c,d] -> [ac,ad, bc, bd]     or      [a,b] [c,d] end -> [ac+end,ad+end, bc+end, bd+end]
            joined_list = []
            for a in list1:
                for b in list2:
                    joined_list.append(a+delimeter+b+end)

            return joined_list

        def generate_octets(octet_filter):
            octet_list = []
            f0 = ''
            f1 = ''
            f2 = ''
            if len(octet_filter) > 0:
                f0 = octet_filter[0]
            else:
                return octet_list
            
            if len(octet_filter) > 1:
                f1 = octet_filter[1]
            else:
                f1 = None
            
            if len(octet_filter) > 2:
                f2 = octet_filter[2]
            else:
                f2 = None

            proposed_f0_list = []
            proposed_f1_list = []
            proposed_f2_list = []

            if f0=='*':
                if f1 is None:
                    # в октете 1 символ - звездочка
                    proposed_f0_list = [str(i) for i in range(0, 256)]
                elif f2 is None:
                    # в октете 2 символа
                    proposed_f0_list = [str(i) for i in range(1, 26)]
                else:
                    # в октете 3 символа
                    proposed_f0_list = [str(i) for i in range(1, 10)]
            elif f0 == '?':
                if f1 is None:
                    # в октете 1 символ - вопрос
                    proposed_f0_list = [str(i) for i in range(0, 10)]
                else:
                    # в октете больше 1 символа
                    proposed_f0_list = [str(i) for i in range(1, 10)]
                
            else:
                # если просто цифра
                proposed_f0_list.append(f0)
                
            if f1 is not None:
                if f1=='*':
                    if f2 is None:
                        # в октете 2 символа
                        proposed_f1_list = [str(i) for i in range(0, 100)]
                    else:
                        # в октете 3 символа
                        proposed_f1_list = [str(i) for i in range(0, 10)]
                elif f1 == '?':
                    proposed_f1_list = [str(i) for i in range(0, 10)]
                else:
                    proposed_f1_list.append(f1)

            if f2 is not None:
                if f2=='*' or f2 == '?':
                  # тк последний октет, то звездочку не растянуть - она по сути то же самое, что и ?
                   proposed_f2_list = [str(i) for i in range(0, 10)]
                else:
                    proposed_f2_list.append(f2)
            
            proposed_octets = proposed_f0_list
            if proposed_f1_list:
                proposed_octets = cross_join(proposed_octets, proposed_f1_list)
                if proposed_f2_list:
                    proposed_octets = cross_join(proposed_octets, proposed_f2_list)
            
            
            for oct in proposed_octets:
                # проверяем, что сгененированное число вообще допустимо для использования в ip адресе
                try:
                    if int(oct) >= 0 and int(oct) < 256:
                        octet_list.append(oct)
                except ValueError:
                    # Handle the exception
                    print(f'{oct} is not an inetger')
                    continue

            return octet_list

        # генерируем для каждого октета все возможные числа из шаблона filter_string
        octet1 = generate_octets(octets[0])
        octet2 = generate_octets(octets[1])
        octet3 = generate_octets(octets[2])
        octet4 = generate_octets(octets[3])

        # делаем cross join для всех 4 окетов между собой
        subn = cross_join(cross_join(cross_join(octet1, octet2, delimeter='.'), octet3, delimeter='.'), octet4, end=mask, delimeter='.')
        return subn


    def update_ipGroups_info(self, cur):
        active_groups = []
        for gr_name, gr in self.config['subnet_group'].items():
            # валидация введенных данных - все поля должны быть заполнены
            if gr is None or gr.get('filter_sting') is None:
                print(f"Warning for subnet_group {gr_name}: group is not valid. Some parameters are blank or dont exists")
                continue
                
            gr_filter = gr['filter_sting']
            active_groups.append(gr_filter)
            # генерируем подсети 
            all_subnets = self.generate_subnet_list(gr_filter)

            s = ""
            bs = ""
            delimeter = "), ("
            for n in all_subnets:
                s+= f"'{n}'"+ delimeter
                bs+= f"'{n}', '{gr_filter}'"+ delimeter
            s = s[:-len(delimeter)]
            bs = bs[:-len(delimeter)]

            cur.execute(f"insert into Ip_Subnet (net_adress) values ({s}) ON CONFLICT DO NOTHING")
            
            group_name_exists = select(self.pg_connection_dict, f"select exists (select true from Ip_Group where group_name='{gr_name}' and group_filter_string != '{gr_filter}')")
            if group_name_exists[0][0]:
                # group_name_exists for different filter string
                cur.execute(f"delete from Ip_Group where group_name='{gr_name}'")
            cur.execute(f"insert into Ip_Group (group_filter_string, group_name) values ('{gr_filter}', '{gr_name}') ON CONFLICT (group_filter_string) DO UPDATE SET group_name=EXCLUDED.group_name")
            
            cur.execute(f"insert into Ip_Subnet_ip_group (net_address, group_filter_string) values ({bs}) ON CONFLICT DO NOTHING")

        # delete all ip_groups that are not listed in settings.yaml
        active_groups_vals = "','".join(active_groups)
        cur.execute(f"delete from Ip_Group where group_filter_string not in ('{active_groups_vals}', '0.0.0.0/0')")


    def exists(self, table_name, where):
        # check if smth exists in table
        s = select(self.pg_connection_dict, f"select exists (select true from {table_name} where {where});")
        return True if s[0][0] else False



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--bare_init', dest="bare_init", default=False, action=argparse.BooleanOptionalAction) #если передали этот флаг - значит не нужно применять миграции
    parser.add_argument('-sf', '--settings_file', dest="settings_file", default='Initializer/settings.yml', type=str) #путь до файла с настройками
    args = parser.parse_args()

    with open(str(args.settings_file)) as f:
        config = yaml.safe_load(f)

    initializer = Initializer(config=config) 
    initializer.initialize()

    if args.bare_init:
        return
    
    initializer.update()    
    initializer.update_settings()



if __name__ == '__main__':
    main()

