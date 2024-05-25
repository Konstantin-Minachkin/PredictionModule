from keras.models import Sequential
from keras.layers import Dense, GRU, Dropout

from LSTM_model import LSTM_model
import sys
from keras.losses import LogCosh, Huber, MeanSquaredError, MeanSquaredLogarithmicError, LogCosh, CosineSimilarity, MeanAbsolutePercentageError, CategoricalCrossentropy, BinaryCrossentropy


class GRU_model(LSTM_model):


    def __init__(self, inp_shape = None, n_outputs = 1, neuro_ammount = 55, loss_f="Huber", loaded_model = None, epochs = 50, batch_size=32, repeat=1, dropout_percent = 0.25, **kwargs):

        if loaded_model is None and inp_shape is None:
            raise ValueError('loaded_model and inp_shape should not be None at the same time!')

        if loaded_model is not None:
            if not isinstance(loaded_model, Sequential):
                raise ValueError('loaded_model should be of Sequential type!')
            self.model = loaded_model
            # self.model.summary()
            return None
        
        self.neuro_ammount = neuro_ammount
        self.loss_f = getattr(sys.modules[__name__], loss_f)()
        self.epochs = epochs
        self.batch_size = batch_size
        self.repeat = repeat
        self.dropout_percent = dropout_percent
        self.n_outputs = n_outputs
        self.inp_shape = inp_shape

        self.model = Sequential()
        self.model.add(GRU (units = self.neuro_ammount, return_sequences = True, input_shape = inp_shape) )
        self.model.add(Dropout(self.dropout_percent))
        self.model.add(GRU (units = self.neuro_ammount))
        self.model.add(Dropout(self.dropout_percent))
        self.model.add(Dense(units = n_outputs))
        
        self.model.compile(optimizer='adam', loss=self.loss_f)
        # self.model.summary()

    def generate_model_filename(self, dop_file_name=None):
        if dop_file_name is None:
            dop_file_name = f"{self.dropout_percent}"
        else:
            dop_file_name = f"{self.dropout_percent}-{dop_file_name}"
        return super().generate_model_filename(dop_file_name=dop_file_name)


    def set_params(self, dropout_percent = 0.25, **kwargs):
        self.dropout_percent = dropout_percent
        super().set_params(**kwargs)


    def save(self, directory_to_save, dop_file_name=None):
        if dop_file_name is None:
            dop_file_name = f"{self.dropout_percent}"
        else:
            dop_file_name = f"{self.dropout_percent}-{dop_file_name}"
        return super().save(directory_to_save, dop_file_name=dop_file_name)

    