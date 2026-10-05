"""neo_golden.py -- the golden models and packers the cocotb tests share, lifted from sim/ (no neosim dependency)."""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "sim"))
from run_tiles import (pack_words, direct_conv, ecc_encode, ins, bits, sbits,  # noqa
                       OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_DRAIN_PSUM, OP_GO, OP_WAIT_FREE, OP_WAIT_DONE, OP_END,
                       OP_NOTIFY, OP_WAIT_RDY, OP_WAIT_REDUCE)
