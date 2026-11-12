"""Niimbot D110 / D11 标签打印协议与客户端。

协议来自对 NIIMBOT 打印机的逆向工程（参考 niimbluelib 的封包定义），
封包格式：``55 55 CMD LEN DATA... XOR 0xAA 0xAA``。
"""

from .packet import NiimbotPacket, PacketError
from .encoder import EncodedImage, ImageRow, encode_image
from .client import NiimbotClient, PrintOptions, PrinterError, PrintCancelled

__all__ = [
    "NiimbotPacket",
    "PacketError",
    "EncodedImage",
    "ImageRow",
    "encode_image",
    "NiimbotClient",
    "PrintOptions",
    "PrinterError",
    "PrintCancelled",
]
