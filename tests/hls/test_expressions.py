from pygears import gear
from pygears.lib import directed, drv
from pygears.sim import sim
from pygears.typing import Bool, Uint, Tuple


def test_inline_if(cosim_cls):
    @gear(hdl={'compile': True})
    async def inv(din: Bool) -> Bool:
        async with din as data:
            yield 0 if data else 1

    directed(drv(t=Bool, seq=[1, 0]), f=inv(sim_cls=cosim_cls), ref=[0, 1])

    sim()


def test_expr_index(cosim_cls):
    @gear(hdl={'compile': True})
    async def test(din: Tuple[Uint[4], Uint[3]]) -> Bool:
        async with din as (data, i):
            yield (data @ data)[i]

    directed(drv(t=Tuple[Uint[4], Uint[3]], seq=[(0xa, i) for i in range(8)]),
             f=test(sim_cls=cosim_cls),
             ref=[0, 1, 0, 1, 0, 1, 0, 1])

    sim()


def test_list_comprehension(cosim_cls):
    @gear(hdl={'compile': True})
    async def test(din: Tuple[Uint[4], Uint[4], Uint[4], Uint[4]]
                   ) -> Tuple[Uint[5], Uint[5], Uint[5], Uint[5]]:
        async with din as d:
            yield [di + 1 for di in d]

    directed(drv(t=Tuple[Uint[4], Uint[4], Uint[4], Uint[4]], seq=[(i, ) * 4 for i in range(8)]),
             f=test(sim_cls=cosim_cls),
             ref=[(i + 1, ) * 4 for i in range(8)])

    sim()
