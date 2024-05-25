from keras.models import load_model
import sys
from GRU import GRU_model
from LSTM_model import LSTM_model

class ModelLoader():

    def str_to_class(classname):
        return getattr(sys.modules[__name__], classname)

    @classmethod
    def load(self, model_file, model_type, weights_file = None, **kwargs):
        
        # передавать все главбльные параметры, как global_params*

        # Recreate the exact same model, including its weights and the optimizer
        model = load_model(model_file)

        new_model = self.str_to_class(model_type)(loaded_model = model, **kwargs)
        if weights_file is not None:
            new_model.model.load_weights(weights_file)    

        print(f"Info: loaded model {new_model.model}")
        return new_model


    @classmethod
    def create_model(self, model_type, **kwargs):
        #создать модель в зависимости от ее типа
        model_obj = self.str_to_class(model_type)(**kwargs)
        return model_obj