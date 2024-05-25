
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler, StandardScaler, RobustScaler
import matplotlib.pyplot as plt
from datetime import datetime
import ipaddress

'''
from pytorch_forecasting import TimeSeriesDataSet
from pytorch_forecasting.data import NaNLabelEncoder
'''

class Data:
    raw_data = pd.DataFrame()

    def __init__(self, csv_file=None):
    #    self.raw_data = pd.read_csv(csv_file, usecols=[0, 2], delimiter=',')
       self.raw_data = pd.read_csv(csv_file, delimiter=',')
    #    self.train_scaler = 

       self.scaler = StandardScaler()
    #    MinMaxScaler(feature_range=(0, 1))
    # RobustScaler()
    

    def prepare_data(self, seq = 1, target_model = 'lstm', n_test_records = 1, batch_size = 128):
    # --готовим дату для обучения
    # Результат - две выборки обуч и тест, а также класс выборок - для обучения и для проверки теста

        if 'time' in self.raw_data.columns:
            t = list()
            for d in self.raw_data.time.values:
                date = datetime.fromisoformat(d).timestamp()
                t.append(date)
            self.raw_data.time = pd.Series(t)

        ip_col = []
        if 'ip' in self.raw_data.columns:
            ip_col.append('ip')
        if 'ip_src' in self.raw_data.columns:
            ip_col.append('ip_src')
        if 'ip_dst' in self.raw_data.columns:
            ip_col.append('ip_dst')

        for ip in ip_col:
            t = list()
            for d in self.raw_data[ip].values:
                ip_adr = int(ipaddress.IPv4Address(d))
                t.append(ip_adr)
            self.raw_data[ip] = pd.Series(t)


        # Нормализуем данные. Столбцы с фиктивными переменными не нормализуем
        dataset = self.raw_data.values.astype('float32')


        # frame as supervised learning
        reframed = self.series_to_supervised(dataset, seq, n_test_records)
        # # drop columns we don't want to predict
        # drop 2 и 3 столбцы с конца (те var1 и var2)
        reframed.drop(reframed.columns[[-2, -3]], axis=1, inplace=True)

        # split into train and test sets
        self.train_dataset = reframed.values[:-n_test_records, :]
        self.test_dataset = reframed.values[-n_test_records:, :]

        self.train_x, self.train_y = self.normalize_data(self.train_dataset, self.train_scaler, target_model)
        self.test_x, self.test_y = self.normalize_data(self.test_dataset, self.test_scaler, target_model)



    def normalize_data(self, data, scaler, target_model = 'lstm'):
        # normalize data
        reframed = scaler.fit_transform(data)
        # выделяем последний столбец в Y
        x, y = reframed[:, :-1], reframed[:, -1]
        if target_model == 'lstm':
            # reshape input to be 3D [samples, timesteps, features]
            x = x.reshape((x.shape[0], 1, x.shape[1]))
        elif target_model == 'enc_dec':
            # for encoder decoder
            #меняем размерность массива на (1, n_steps_in, n_features)
            x = np.reshape(x, (x.shape[0], 1, x.shape[1]) )
        else:
            # for statefull lstm
            #меняем размерность массива на (samples, features, 1)
            x = np.reshape(x, (x.shape[0], x.shape[1], 1 ))
        return x, y
    
    

    # convert series to supervised learning
    def series_to_supervised(self, data, n_in=1, n_out=1, dropnan=True):
        n_vars = 1 if type(data) is list else data.shape[1]
        df = pd.DataFrame(data)
        cols, names = list(), list()
        # input sequence (t-n, ... t-1)
        for i in range(n_in, 0, -1):
            cols.append(df.shift(i))
            names += [('var%d(t-%d)' % (j+1, i)) for j in range(n_vars)]
        # forecast sequence (t, t+1, ... t+n)
        for i in range(0, n_out):
            cols.append(df.shift(-i))
            if i == 0:
                names += [('var%d(t)' % (j+1)) for j in range(n_vars)]
            else:
                names += [('var%d(t+%d)' % (j+1, i)) for j in range(n_vars)]
        
        # put it all together
        agg = pd.concat(cols, axis=1)
        agg.columns = names
        # drop rows with NaN values
        if dropnan:
            agg.dropna(inplace=True)
        return agg

