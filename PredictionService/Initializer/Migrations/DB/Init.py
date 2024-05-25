
from ..MigrationBase import migration

class InitDB():
    
    # если используем transation - то функции apply надо передавать не connection_obj, а connection_dictionary
    # возваращает false - если что-то пошло не так
    @migration
    # connection_obj is a connectionObject returned from psycopg2 connect()
    def apply(self, cursor, **kwargs):
        # создать под миграции в DB табличку
        cursor.execute('''CREATE TABLE IF NOT EXISTS _migrations (
                            name                  text PRIMARY KEY,
                            time_when_applied     timestamp DEFAULT Now()
                        );''')
        
        # создать новые таблички\схемы\проч объекты
        cursor.execute('''CREATE TABLE IF NOT EXISTS Settings (
                            param_name    text PRIMARY KEY,
                            param_type    text CHECK (param_type IN ('string', 'int') ),
                            value_txt    text,
                            value_int    int
                        );''')
        

        cursor.execute('''CREATE TABLE IF NOT EXISTS Port (
                            port_type           text CHECK (port_type IN ('tcp', 'udp') ),
                            port_num            int CHECK (0 < port_num and port_num < 65535 ),

                            CONSTRAINT Port_Pkey PRIMARY KEY (port_type, port_num)
                        );''')
        

        cursor.execute('''CREATE TABLE IF NOT EXISTS Port_group (
                            group_name              text PRIMARY KEY
                        );''')
        cursor.execute('''INSERT INTO Port_group (group_name) values ('any-tcp'), ('any-udp'), ('any')
                        ;''')
        

        cursor.execute('''CREATE TABLE IF NOT EXISTS Port_Port_group (
                            group_name              text REFERENCES Port_group (group_name) ON UPDATE CASCADE ON DELETE CASCADE
                            , port_type             text
                            , port_num              int
                            
                            , FOREIGN KEY (port_type, port_num) REFERENCES Port (port_type, port_num) ON UPDATE CASCADE ON DELETE CASCADE

                            , CONSTRAINT Port_Port_group_Pkey PRIMARY KEY (group_name, port_type, port_num)
                        );''')
        

        cursor.execute('''CREATE TABLE IF NOT EXISTS Ip_Subnet (
                            net_adress      inet PRIMARY KEY
                        );''')
        cursor.execute('''INSERT INTO Ip_Subnet (net_adress) values ('0.0.0.0/0')
                        ;''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS Ip_Group (
                            group_filter_string     text PRIMARY KEY,
                            group_name              text UNIQUE
                        );''')
        cursor.execute('''INSERT INTO Ip_Group (group_filter_string, group_name) values ('0.0.0.0/0', 'any')
                        ;''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS Ip_Subnet_ip_group (
                            net_address             inet REFERENCES Ip_Subnet (net_adress) ON UPDATE CASCADE ON DELETE CASCADE,
                            group_filter_string     text REFERENCES Ip_Group (group_filter_string) ON UPDATE CASCADE ON DELETE CASCADE,

                            CONSTRAINT Ip_Subnet_ip_group_Pkey PRIMARY KEY (net_address, group_filter_string)
                        );''')
        cursor.execute('''INSERT INTO Ip_Subnet_ip_group (group_filter_string, net_address) values ('0.0.0.0/0', inet '0.0.0.0/0')
                        ;''')
        
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS Package_Type (
                            ip_src              text REFERENCES Ip_Group (group_filter_string) ON UPDATE CASCADE ON DELETE CASCADE
                            , ip_dst              text REFERENCES Ip_Group (group_filter_string) ON UPDATE CASCADE ON DELETE CASCADE
                            , port_group_id       text REFERENCES Port_group (group_name) ON UPDATE CASCADE ON DELETE CASCADE
                            , is_monitored        boolean DEFAULT true,

                            CONSTRAINT PackageType_Pkey PRIMARY KEY (ip_src, ip_dst, port_group_id)
                        );''')
        

        cursor.execute('''CREATE TABLE IF NOT EXISTS Package_header (
                            ip_src                inet
                            , ip_dst              inet
                            , port_type           text
                            , port_num            int

                            , FOREIGN KEY (port_type, port_num) REFERENCES Port (port_type, port_num)
                            , CONSTRAINT Package_header_Pkey PRIMARY KEY (ip_src, ip_dst, port_type, port_num)
                        );''')


        cursor.execute('''CREATE TABLE IF NOT EXISTS Package (
                            ip_src                inet
                            , ip_dst              inet
                            , port_type           text
                            , port_num            int
                            , time                timestamp
                            , log_flag_series_num   int DEFAULT 0
                            , log_flag              int DEFAULT 0

                            , FOREIGN KEY (ip_src, ip_dst, port_type, port_num) REFERENCES Package_header(ip_src, ip_dst, port_type, port_num) ON UPDATE CASCADE ON DELETE CASCADE
                            , CONSTRAINT Package_Pkey PRIMARY KEY (ip_src, ip_dst, port_type, port_num, time)
                        );''')
        

        cursor.execute('''CREATE TABLE IF NOT EXISTS Package_header_Package_Type (
                            pType_ip_src              text
                            , pType_ip_dst              text
                            , pType_port_group_id       text
                            , ip_src                    inet
                            , ip_dst                    inet
                            , port_type                 text
                            , port_num                  int

                            , FOREIGN KEY (pType_ip_src, pType_ip_dst, pType_port_group_id) REFERENCES Package_Type(ip_src, ip_dst, port_group_id) ON UPDATE CASCADE ON DELETE CASCADE
                            , FOREIGN KEY (ip_src, ip_dst, port_type, port_num) REFERENCES Package_header(ip_src, ip_dst, port_type, port_num) ON UPDATE CASCADE ON DELETE CASCADE

                            , CONSTRAINT Package_Package_Type_Pkey PRIMARY KEY (pType_ip_src, pType_ip_dst, pType_port_group_id, ip_src, ip_dst, port_type, port_num)
                        );''')
        
        # id                        int GENERATED ALWAYS AS IDENTITY PRIMARY KEY

        cursor.execute('''CREATE OR REPLACE FUNCTION select_int_setting(sett_name text) 
                        RETURNS int LANGUAGE SQL AS $$ SELECT value_int FROM settings WHERE param_name = sett_name and param_type='int'; $$;

                        CREATE OR REPLACE FUNCTION select_text_setting(sett_name text) 
                        RETURNS text LANGUAGE SQL AS $$ SELECT value_txt FROM settings WHERE param_name = sett_name and param_type='string'; $$;
                    ''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS Model (
                            name                    text PRIMARY KEY
                            , model_type            text
                            , loss_f                text
                            , seq_length            int
                            , prediction_seq_size   int DEFAULT select_int_setting('prediction-size')
                            , epochs                int
                            , repeat                int
                            , batch_size            int
                            , neurons_ammount       int
                            , dropout_percent       float DEFAULT null
                            , valid_seq_len         int DEFAULT select_int_setting('validation_data_ammount') 
                            , min_accuracy          int DEFAULT select_int_setting('min_accuracy')
                            , max_dataset_size      int DEFAULT select_int_setting('max_dataset_size') 
                            , min_dataset_size      int DEFAULT select_int_setting('min_dataset_size')
                        );''')


        cursor.execute('''CREATE TABLE IF NOT EXISTS Package_Predictor (
                            pType_ip_src              text
                            , pType_ip_dst              text
                            , pType_port_group_id       text
                            , specific_ip_dst           inet CHECK (masklen(specific_ip_dst)=32)
                            , use_model                text REFERENCES Model(name) ON UPDATE CASCADE ON DELETE SET DEFAULT 
                                DEFAULT select_text_setting('default_model')
                            , accuracy                  float DEFAULT 0
                            , total_hits                int DEFAULT 0
                            , validation_accuracy       float DEFAULT null
                            , time_of_train             float DEFAULT null
                            , current_log_flag_series_num       int DEFAULT 0
                            , counted_log_flag                 int DEFAULT -1
                            , counted_log_flag_series_num      int DEFAULT -1
                            , model_file_url            text DEFAULT null
                            , scaler_file_url           text DEFAULT null
                            , scaler_name               text DEFAULT select_text_setting('default_scaler')
                            , current_dataset_size      int DEFAULT 0

                            , FOREIGN KEY (pType_ip_src, pType_ip_dst, pType_port_group_id) REFERENCES Package_Type(ip_src, ip_dst, port_group_id) ON UPDATE CASCADE ON DELETE CASCADE

                            , CONSTRAINT Package_Predictor_Pkey PRIMARY KEY (pType_ip_src, pType_ip_dst, pType_port_group_id, specific_ip_dst)
                        );''')

        # current_log_flag - это текущий лог флаг, которым маркируется пакет    
            # , current_log_flag                  int DEFAULT 0 - убрал, тк по идее не нужен, он будет постоянно сбрасываться в 0
        # counted_log_flag_series_num - номер серии - для которой не проводился расчет точности, но для всех предыдущих серий - уже проведен


        # не используем триггеры, тк с ними все ооooooчень медленно работает
        # cursor.execute('''CREATE OR REPLACE FUNCTION set_Package_Predictor_to_default() RETURNS TRIGGER AS $$
        #             BEGIN
        #                 UPDATE Package_Predictor SET accuracy=0, time_of_train=null
        #                     where pType_ip_src = NEW.pType_ip_src and pType_ip_dst = NEW.pType_ip_dst and pType_port_group_id = NEW.pType_port_group_id;
        #                 RETURN NULL;
        #             END;
        #             $$ LANGUAGE plpgsql;
        #             ''')

        # # если используемая модель изменилась - сбросить показатели по ней в default log flag не меняем! он должен оставаться неизменным
        # cursor.execute('''CREATE OR REPLACE TRIGGER check_update_Predictor_model
        #                 AFTER UPDATE ON Package_Predictor
        #                 FOR EACH ROW
        #                 WHEN (OLD.use_model IS DISTINCT FROM NEW.use_model)
        #                 EXECUTE PROCEDURE set_Package_Predictor_to_default();
        #                 ;''')


        
        # FOREIGN KEY REFERENCES Package_Predictor нельзя будет удалить predictor (например, если конфигурация изменится) пока в Predicted_Package будут оставаться таблицы, предсказанные им
        cursor.execute('''CREATE TABLE IF NOT EXISTS Predicted_Package (
                            ip_src                  inet
                            , ip_dst                inet
                            , port_type             text
                            , port_num              int
                            , predicted_time        timestamp
                            , predicted_time_diff   float
                            , log_flag              int
                            , log_flag_series_num   int
                            , was_predicted         boolean DEFAULT false
                            , was_hited             boolean DEFAULT false
                            , predicted_by_pType_ip_src         text          
                            , predicted_by_pType_ip_dst         text
                            , predicted_by_pType_port_group_id  text
                            

                            , FOREIGN KEY (ip_src, ip_dst, port_type, port_num) REFERENCES Package_header(ip_src, ip_dst, port_type, port_num) ON UPDATE CASCADE ON DELETE CASCADE
                            , FOREIGN KEY (predicted_by_pType_ip_src, predicted_by_pType_ip_dst, predicted_by_pType_port_group_id, ip_dst) REFERENCES Package_Predictor(pType_ip_src, pType_ip_dst, pType_port_group_id, specific_ip_dst)
                            , CONSTRAINT Predicted_Package_Pkey PRIMARY KEY (ip_src, ip_dst, port_type, port_num, predicted_time)
                        );''')

        # was_hited - если пакет был угадан
        # was_predicted - если пакет был отправлен на запись флоу потоков на контроллер

        