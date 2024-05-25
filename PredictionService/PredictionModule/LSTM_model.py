from keras.models import Sequential
from keras.layers import Dense, LSTM
import keras.backend as kb
from math import sqrt
from sklearn.metrics import mean_squared_error
import numpy as np
from time import time_ns, time
import sys
from keras.losses import LogCosh, Huber, MeanSquaredError, MeanSquaredLogarithmicError, LogCosh, CosineSimilarity, MeanAbsolutePercentageError, CategoricalCrossentropy, BinaryCrossentropy


class LSTM_model():

    def __init__(self, inp_shape = None, n_outputs = 1, loaded_model = None, loss_f="LogCosh", neuro_ammount=128, epochs = 50, batch_size=32, repeat=1, **kwargs):
        # в kwargs попадут все остальные аргументы, которые не нужны
        
        if loaded_model is None and inp_shape is None:
            raise ValueError('loaded_model and inp_shape should not be None at the same time!')

        if loaded_model is not None:
            # загружаем ТОЛЬКО модель
            if not isinstance(loaded_model, Sequential):
                raise ValueError('loaded_model should be of Sequential type!')
            self.model = loaded_model
            # self.model.summary()
            return None

        self.loss_f = getattr(sys.modules[__name__], loss_f)()
        self.neuro_ammount = neuro_ammount
        self.epochs = epochs
        self.batch_size = batch_size
        self.repeat = repeat
        self.n_outputs = n_outputs
        self.inp_shape = inp_shape

        # сеть с одним входным, выходным слоем и скрытым LSTM слоем
        self.model = Sequential()
        self.model.add(LSTM(self.neuro_ammount, activation='relu', input_shape=inp_shape))      # input_shape=(1, n_features)
        self.model.add(Dense(n_outputs))    # одномерный ряд, поэтому число функций = одной для одной переменной

        self.model.compile(loss=self.loss_f, optimizer='adam')
        # self.model.summary() #структура модели
        

    def set_params(self, inp_shape = None, n_outputs = 1, loss_f="LogCosh", neuro_ammount=128, epochs = 50, batch_size=32, repeat=1, **kwargs):
        self.loss_f = getattr(sys.modules[__name__], loss_f)()
        self.neuro_ammount = neuro_ammount
        self.epochs = epochs
        self.batch_size = batch_size
        self.repeat = repeat
        self.n_outputs = n_outputs
        self.inp_shape = inp_shape


    def predict(self, x, scaler):
        prediction = self.model.predict(x) #, verbose=0

        # invert predictions
        # матрицу x (кроме последнего столбца) + prediction
        # нарезать для каждой строки (:)(и повторяется два раза, тк массив 3d) все столбцы кроме последнего (его заменим новым = prediction)
        # превращаем массив х, который 2d, в массив reshaped_x, который 2d
        # reshaped_x = x.reshape((x.shape[0], x.shape[2]))
        # prediction = np.concatenate((reshaped_x, prediction), axis=1)  
        prediction = scaler.inverse_transform(prediction)
        # prediction - это nupmy.ndarray ?
        return prediction
    

    def train(self, x,y, epochs = None, batch_size=None, repeat = None):
        if epochs is None:
            epochs = 100 if self.epochs is None else self.epochs
        if batch_size is None:
            batch_size = 32 if self.batch_size is None else self.batch_size
        if repeat is None:
            repeat = 1 if self.repeat is None else self.repeat
        
        t_start = time_ns()/1000000
        for _ in range(repeat):
            self.model.fit(x, y, epochs=epochs, batch_size=batch_size, verbose=0, shuffle=False, validation_data=(x, y))
            # validation_split=0.9, 
            # if repeat > 1: 
            #     self.model.reset_states()
        t_end = time_ns()/1000000
        self.t_tr = (t_end - t_start)/1000 #time in seconds time_of_train net
        return self.t_tr


    def save(self, directory_to_save, dop_file_name=None, saveWeghts = False):
        # метод сохраняет модель в место на диске и возвращает сохраненное имя модели - генерируется автоматически на основе имени модели
        # generate filename
        filename = f"{directory_to_save}"
        filename += self.generate_model_filename()
        if dop_file_name is not None:
            dop_file_name = ''.join(dop_file_name.split()).replace('.','_').replace('/','__').replace('(','').replace(')','').replace(',','_')  #remove
            filename += '-'+dop_file_name

        if saveWeghts:
            weights_filename = filename+".weights.h5"
        else:
            weights_filename = None

        filename+= ".keras"

        # Save the entire model
        # файл с тем же именем - перезапишется
        self.model.save(filename)
        
        if weights_filename is not None:
            self.model.save_weights(weights_filename)

        return filename, weights_filename

    def generate_model_filename(self, dop_file_name=None):
        name = type(self).__name__ + f"-{self.inp_shape}-{self.n_outputs}-{self.neuro_ammount}-{self.model.loss.name}-{self.epochs}-{self.batch_size}-{self.repeat}"
        if dop_file_name is not None:
            name += '-'+dop_file_name
        name = ''.join(name.split()).replace('.','_').replace('/','__').replace('(','').replace(')','').replace(',','_')  #remove wrong symbols
        return name


    def count_error(self, y_real, y_pred, max_time_diff=480, time_make_way = 120):
        # берем для каждого из массивов (они 2d, потому одно :) 3 столбец
        # выведем процент ошибочных прогнозов (которые предсказали позже либо сильно раньше чем нужно) самопальным методом
        error_y = [y1-y2 for y1, y2 in zip(y_real, y_pred)]

        # for i in error_y:
        #     if i < 0 эт плохо, тк предсказанное знач больше нужного
        #    но так как контроллер построит путь не в момент у_предскз, а чуть раньше (в у_пред - Х), то может быть ситуацию когда у_пред > у_реал на N, и N < Х  - то есть путь построится все же раньше чем пакет появится в сети
        #    поэтому вместо 0 введем значение = 0 - ( X - M  ) где М - время, за которое контроллер построит путь для пакета и настроит все свитчи. И лучше чтобы М было < X
        #     if i > 0 но больше например 30 эт тоже плохо, тк предсказанное знач сильно меньше нужного
        #     if i > 0 и меньше например 30 - то ок - нам подходит
        # [function1(item) for item in iterable if function2(item)]

        hits = [x for x in error_y if (abs(x) < (time_make_way+max_time_diff))] #разница времени(x), если она меньше 0 должно быть < (time_make_way+max_time_diff), а если больше 0 (то есть y_pred > y_real, спрогноизровали, что пакет появится в сети раньше, чем в реальности будет), то должно быть как минимум 
        # (x > 0 - (time_make_way+max_time_diff) and x < max_time_diff - time_make_way)]
        local_accuracy = len(hits)/len(error_y)*100

        # score = sqrt(mean_squared_error(y_real, y_pred))
        # print('Score: %.2f RMSE' % (score))
        # huber_score = keras.losses.huber(y_real, y_pred, delta=1.0)
        # print('Huber = ', huber_score)
        return hits, local_accuracy
        


    def create_plot(self, dataset, prediction, log_to_console = False):
        
        pred_pl, real_pl = [], []
        for num in prediction[:, -1]:
            pred_pl.append(round(num, 2))
        
        for num in dataset[:, -1]:
            real_pl.append(round(num, 2))

        if log_to_console:
            print("real y ", real_pl)
            print("pred y ", pred_pl)

        return pred_pl, real_pl
    

    