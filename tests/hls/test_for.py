from pygears import gear
from pygears.lib import drv
from pygears.sim import sim, cosim
from pygears.typing import Array, Bool, Uint, code, Queue
from pygears.lib import directed
from pygears.util.utils import qrange


def test_simple(sim_cls):
    @gear(hdl={'compile': True})
    async def test() -> Uint[3]:
        for i in range(4):
            yield i

    directed(f=test(sim_cls=sim_cls), ref=list(range(4)) * 2)

    sim(timeout=8)


def test_simple_qrange(sim_cls):
    @gear(hdl={'compile': True})
    async def test() -> Queue[Uint[3]]:
        for i, last in qrange(4):
            yield i, last

    directed(f=test(sim_cls=sim_cls), ref=[list(range(4))] * 2)

    sim(timeout=8)


def test_unfold(lang):
    @gear(hdl={'compile': True})
    async def test() -> Array[Uint[3], 4]:
        data = Array[Uint[3], 4](None)
        for i in range(4):
            data[i] = i

        yield data

    directed(f=test(), ref=[(0, 1, 2, 3)] * 2)

    cosim('/test', 'verilator', lang=lang)
    sim(timeout=2)


def test_unfold_uint(lang):
    @gear(hdl={'compile': True})
    async def test(din: Bool, *, w_dout) -> Uint['w_dout']:
        data = Array[Bool, w_dout](None)
        async with din as d:
            for i in range(w_dout):
                data[i] = d

            yield code(data, Uint)

    directed(drv(t=Bool, seq=[0, 1]), f=test(w_dout=8), ref=[0x00, 0xff])

    cosim('/test', 'verilator', lang=lang)
    sim(timeout=2)


# def test_comprehension():
#     @gear(hdl={'compile': True})
#     async def test() -> Array[Uint[3], 4]:
#         yield Array[Uint[3], 4](i for i in range(4))

#     directed(f=test(), ref=[(0, 1, 2, 3)] * 2)

#     cosim('/test', 'verilator', lang=lang)
#     sim(timeout=2)
