"""G.711 mu-law: the 8-bit encoding telephone networks (and Deepgram's telephone mode) use, converted to and
from the 16-bit PCM of `AudioFrame`.

It is a codec concern of the telephony side of the house, not of the shared media interface. Written out here
because Python 3.13 no longer ships `audioop`. Both directions are table lookups after a one-off build.
"""

import struct
import sys
from array import array
from functools import cache

BIAS = 0x84
CLIP = 32635  # the largest magnitude mu-law can carry (before the bias)


def _decode_code(code: int) -> int:
    inverted = ~code & 0xFF
    magnitude = ((((inverted & 0x0F) << 3) + BIAS) << ((inverted >> 4) & 0x07)) - BIAS
    return -magnitude if inverted & 0x80 else magnitude


_DECODE = tuple(struct.pack("<h", _decode_code(code)) for code in range(256))


def _encode_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), CLIP) + BIAS
    exponent = magnitude.bit_length() - 8  # the segment: 0 (quietest) to 7 (loudest)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


@cache
def _encode_table() -> bytes:
    return bytes(_encode_sample(sample) for sample in range(-32768, 32768))


def decode(mulaw: bytes) -> bytes:
    """mu-law bytes -> 16-bit little-endian PCM (twice as many bytes)."""
    return b"".join(map(_DECODE.__getitem__, mulaw))


def encode(pcm: bytes) -> bytes:
    """16-bit little-endian PCM -> mu-law bytes. Raises ValueError if `pcm` is not whole 16-bit samples."""
    samples = array("h")
    samples.frombytes(pcm)  # ValueError for an odd number of bytes

    if sys.byteorder == "big":
        samples.byteswap()

    table = _encode_table()
    return bytes(table[sample + 32768] for sample in samples)
