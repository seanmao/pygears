import pytest

from pygears.cookbook.delay import delay_rng
from pygears.cookbook.replicate import replicate
from pygears.cookbook.verif import directed, verif
from pygears.sim import sim
from pygears.sim.modules.drv import drv
from pygears.typing import Tuple, Uint

sequence = [(2, 3), (5, 5), (3, 9), (8, 1)]
ref = list([x[1]] * x[0] for x in sequence)

t_din = Tuple[Uint[16], Uint[16]]


def test_directed(tmpdir, sim_cls):
    directed(drv(t=t_din, seq=sequence), f=replicate(sim_cls=sim_cls), ref=ref)
    sim(outdir=tmpdir)


@pytest.mark.parametrize('din_delay', [0, 1, 10])
@pytest.mark.parametrize('dout_delay', [0, 1, 10])
def test_directed_cosim(tmpdir, sim_cls, din_delay, dout_delay):
    verif(
        drv(t=t_din, seq=sequence) | delay_rng(din_delay, din_delay),
        f=replicate(sim_cls=sim_cls),
        ref=replicate(name='ref_model'),
        delays=[delay_rng(dout_delay, dout_delay)])

    sim(outdir=tmpdir)
