import pytest

from pygears.util.test_utils import synth_check
from pygears.typing import Union, Uint
from pygears.lib import demux, mux, demux_ctrl
from pygears.lib.delay import delay_rng
from pygears.lib.verif import drv, directed
from pygears.sim import sim
from pygears import Intf, gear


@pytest.mark.parametrize('din_delay', [0, 1])
@pytest.mark.parametrize('dout_delay', [0, 1])
@pytest.mark.parametrize('branches', list(range(2, 10)))
def test_simple_directed(sim_cls, din_delay, dout_delay, branches):

    seq = [(i, i) for i in range(branches)]
    TDin = Union[tuple(Uint[i] for i in range(1, branches + 1))]

    directed(
        drv(t=TDin, seq=seq) | delay_rng(din_delay, din_delay),
        f=demux(sim_cls=sim_cls),
        delays=[delay_rng(dout_delay, dout_delay) for _ in range(branches)],
        ref=[[i] for i in range(branches)])

    sim()


@pytest.mark.parametrize('din_delay', [0, 1])
@pytest.mark.parametrize('dout_delay', [0, 1])
@pytest.mark.parametrize('branches', list(range(2, 10)))
def test_mapped_directed(sim_cls, din_delay, dout_delay, branches):

    seq = [(i, i) for i in range(branches)]
    TDin = Union[tuple(Uint[i] for i in range(1, branches + 1))]

    mapping = {}
    for i in range(branches):
        mapping[i] = (i + 1) if (i + 1) < branches else 0

    ref = [[(i - 1) if (i - 1) >= 0 else (branches - 1)]
           for i in range(branches)]

    directed(
        drv(t=TDin, seq=seq) | delay_rng(din_delay, din_delay),
        f=demux(mapping=mapping, sim_cls=sim_cls),
        delays=[delay_rng(dout_delay, dout_delay) for _ in range(branches)],
        ref=ref)

    sim()


@pytest.mark.parametrize('din_delay', [0, 1])
@pytest.mark.parametrize('dout_delay', [0, 1])
def test_mapped_default_directed(sim_cls, din_delay, dout_delay):

    seq = [(i, i) for i in range(8)]
    TDin = Union[tuple(Uint[i] for i in range(1, 8 + 1))]

    mapping = {3: 0, 4: 0, 7: 1}

    ref = [[3, 4], [7], [0, 1, 2, 5, 6]]

    directed(drv(t=TDin, seq=seq) | delay_rng(din_delay, din_delay),
             f=demux(mapping=mapping, sim_cls=sim_cls),
             delays=[delay_rng(dout_delay, dout_delay) for _ in range(3)],
             ref=ref)

    sim()


@pytest.mark.parametrize('branches', [2, 3, 27])
@synth_check({'logic luts': 0, 'ffs': 0}, tool='yosys')
def test_mux_demux_redux_yosys(branches):
    TDin = Union[tuple(Uint[i] for i in range(1, branches + 1))]

    @gear
    def test(din):
        return demux_ctrl(din) | mux

    test(Intf(TDin))
