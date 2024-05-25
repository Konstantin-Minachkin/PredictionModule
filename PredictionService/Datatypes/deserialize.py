# import Package_pb2
# использую такой импорт, тк при первом варианте ошибка, тк вы используем sys.path для подключения всего из папки Datatypes
from Datatypes import Package_pb2, PredictedPackage_pb2, Predictor_pb2, countMsg_pb2

# package_enum_deserialise = {0: 'tcp', 1: 'udp', 2: 'none'}

def decode_packet_protobuff(message, props):
    # returns None if cant decode packet properly
    if props.headers is not None and props.headers.get("proto", False):
        packet = Package_pb2.Package()
        packet.ParseFromString(message)
        return packet
    else:
        print(f"WARNING: message had no 'proto' tag: {message} ")
        return None

def decode_predicted_packet_protobuff(message, props):
    # returns None if cant decode packet properly
    if props.headers is not None and props.headers.get("proto", False):
        packet = PredictedPackage_pb2.PredictedPackage()
        packet.ParseFromString(message)
        return packet
    else:
        print(f"WARNING: message had no 'proto' tag: {message} ")
        return None

def decode_predictor_protobuff(message, props):
    # returns None if cant decode packet properly
    if props.headers is not None and props.headers.get("proto", False):
        pred = Predictor_pb2.Predictor()
        pred.ParseFromString(message)
        return pred
    else:
        print(f"WARNING: message had no 'proto' tag: {message} ")
        return None


def decode_countMsg_protobuff(message, props):
    # returns None if cant decode packet properly
    if props.headers is not None and props.headers.get("proto", False):
        msg = countMsg_pb2.countMsg()
        msg.ParseFromString(message)
        return msg
    else:
        print(f"WARNING: message had no 'proto' tag: {message} ")
        return None