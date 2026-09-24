from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

CONNECTION_FAILED: Status
DESCRIPTOR: _descriptor.FileDescriptor
MALFORMED_MESSAGE: Status
NO_RESERVATION: Status
OK: Status
PERMISSION_DENIED: Status
RESERVATION_REFUSED: Status
RESOURCE_LIMIT_EXCEEDED: Status
UNEXPECTED_MESSAGE: Status
UNUSED: Status

class HopMessage(_message.Message):
    __slots__ = ["limit", "peer", "reservation", "status", "type"]
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
    CONNECT: HopMessage.Type
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    PEER_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_FIELD_NUMBER: _ClassVar[int]
    RESERVE: HopMessage.Type
    STATUS: HopMessage.Type
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    limit: Limit
    peer: Peer
    reservation: Reservation
    status: Status
    type: HopMessage.Type
    def __init__(self, type: _Optional[_Union[HopMessage.Type, str]] = ..., peer: _Optional[_Union[Peer, _Mapping]] = ..., reservation: _Optional[_Union[Reservation, _Mapping]] = ..., limit: _Optional[_Union[Limit, _Mapping]] = ..., status: _Optional[_Union[Status, str]] = ...) -> None: ...

class Limit(_message.Message):
    __slots__ = ["data", "duration"]
    DATA_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    data: int
    duration: int
    def __init__(self, duration: _Optional[int] = ..., data: _Optional[int] = ...) -> None: ...

class Peer(_message.Message):
    __slots__ = ["addrs", "id"]
    ADDRS_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    addrs: _containers.RepeatedScalarFieldContainer[bytes]
    id: bytes
    def __init__(self, id: _Optional[bytes] = ..., addrs: _Optional[_Iterable[bytes]] = ...) -> None: ...

class Reservation(_message.Message):
    __slots__ = ["addrs", "expire", "voucher"]
    ADDRS_FIELD_NUMBER: _ClassVar[int]
    EXPIRE_FIELD_NUMBER: _ClassVar[int]
    VOUCHER_FIELD_NUMBER: _ClassVar[int]
    addrs: _containers.RepeatedScalarFieldContainer[bytes]
    expire: int
    voucher: bytes
    def __init__(self, expire: _Optional[int] = ..., addrs: _Optional[_Iterable[bytes]] = ..., voucher: _Optional[bytes] = ...) -> None: ...

class StopMessage(_message.Message):
    __slots__ = ["limit", "peer", "status", "type"]
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
    CONNECT: StopMessage.Type
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    PEER_FIELD_NUMBER: _ClassVar[int]
    STATUS: StopMessage.Type
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    limit: Limit
    peer: Peer
    status: Status
    type: StopMessage.Type
    def __init__(self, type: _Optional[_Union[StopMessage.Type, str]] = ..., peer: _Optional[_Union[Peer, _Mapping]] = ..., limit: _Optional[_Union[Limit, _Mapping]] = ..., status: _Optional[_Union[Status, str]] = ...) -> None: ...

class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
