# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: Package.proto
"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\rPackage.proto\"\x96\x01\n\x07Package\x12\x0e\n\x06ip_src\x18\x01 \x01(\t\x12\x0e\n\x06ip_dst\x18\x02 \x01(\t\x12\x0c\n\x04port\x18\x03 \x01(\x05\x12\x1c\n\tport_type\x18\x04 \x01(\x0e\x32\t.portType\x12\x10\n\x08timestap\x18\x05 \x01(\t\x12\x10\n\x08log_flag\x18\x06 \x01(\x05\x12\x1b\n\x13log_flag_series_num\x18\x07 \x01(\x03*&\n\x08portType\x12\x07\n\x03tcp\x10\x00\x12\x07\n\x03udp\x10\x01\x12\x08\n\x04none\x10\x02\x62\x06proto3')

_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'Package_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:

  DESCRIPTOR._options = None
  _PORTTYPE._serialized_start=170
  _PORTTYPE._serialized_end=208
  _PACKAGE._serialized_start=18
  _PACKAGE._serialized_end=168
# @@protoc_insertion_point(module_scope)