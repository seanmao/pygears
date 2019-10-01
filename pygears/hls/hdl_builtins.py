import ast
from functools import reduce
from pygears.typing import Int, Tuple, Uint, typeof, Queue, is_type, div, floor
from pygears.typing.queue import QueueMeta

from pygears.util.utils import gather, qrange
from pygears.sim import clk
from pygears import Intf, cast, signed
from pygears.core.gear import OutSig

from .hls_expressions import ArrayOpExpr, AttrExpr, BinOpExpr, CastExpr
from .hls_expressions import ConcatExpr, ConditionalExpr, IntfDef, ResExpr
from .hls_expressions import UnaryOpExpr, TupleExpr, SignalDef, SignalStmt
from .hdl_arith import resolve_cast_func


def call_floor(arg):
    t_arg = arg.dtype
    int_cls = Int if t_arg.signed else Uint
    arg_to_int = CastExpr(arg, int_cls[t_arg.width])
    if t_arg.fract >= 0:
        return BinOpExpr((arg_to_int, ResExpr(Uint(t_arg.fract))), '>>')
    else:
        return BinOpExpr((arg_to_int, ResExpr(Uint(-t_arg.fract))), '<<')


def call_div(a, b, subprec):
    t_a = a.dtype
    t_b = b.dtype

    t_div = div(t_a, t_b, int(subprec.val))

    def fixp__div__(op1: t_a, op2: t_b) -> t_div:
        return t_div(op1) // op2

    return fixp__div__


def max_expr(op1, op2):
    op1_compare = op1
    op2_compare = op2
    signed = typeof(op1.dtype, Int) or typeof(op2.dtype, Int)
    if signed and typeof(op1.dtype, Uint):
        op1_compare = CastExpr(op1, Int[int(op1.dtype) + 1])
    if signed and typeof(op2.dtype, Uint):
        op2_compare = CastExpr(op2, Int[int(op2.dtype) + 1])

    cond = BinOpExpr((op1_compare, op2_compare), '>')
    return ConditionalExpr(cond=cond, operands=(op1, op2))


def precompiled(func):
    func.__precompiled__ = True
    return func


def call_len(arg, **kwds):
    return ResExpr(len(arg.dtype))


def call_print(arg, **kwds):
    pass


def call_int(arg, **kwds):
    # ignore cast
    if typeof(arg.dtype, (Uint, Int)):
        return arg
    else:
        return CastExpr(arg, cast_to=Uint[len(arg.dtype)])


def call_all(arg, **kwds):
    return ArrayOpExpr(arg, '&')


def call_max(*arg, **kwds):
    if len(arg) != 1:
        return reduce(max_expr, arg)

    arg = arg[0]

    assert isinstance(arg.op, IntfDef), 'Not supported yet...'
    assert typeof(arg.dtype, Tuple), 'Not supported yet...'

    op = []
    for field in arg.dtype.fields:
        op.append(AttrExpr(arg.op, [field]))

    return reduce(max_expr, op)


def call_sub(obj, arg):
    return CastExpr(arg, cast_to=obj.sub())


def outsig_write(obj, arg):
    return SignalStmt(SignalDef(obj), arg)


def call_get(obj, *args, **kwds):
    return obj


def call_get_nb(obj, *args, **kwds):
    return obj


def call_clk(*arg, **kwds):
    return None


def call_empty(obj, *arg, **kwds):
    assert not arg, 'Empty should be called without arguments'
    expr = IntfDef(intf=obj.intf, _name=obj.name, context='valid')
    return UnaryOpExpr(expr, '!')


def call_gather(*arg, **kwds):
    return ConcatExpr(operands=list(arg))


def call_cast(dtype, cast_type):
    return resolve_cast_func(cast_type.val, dtype)


def call_signed(val):
    if val.dtype.signed:
        return val

    if typeof(val.dtype, Uint):
        return resolve_cast_func(Int, val)

    raise Exception("Unsupported signed cast")


builtins = {
    gather: call_gather,
    all: call_all,
    max: call_max,
    clk: call_clk,
    int: call_int,
    len: call_len,
    print: call_print,
    div: call_div,
    floor: call_floor,
    Intf.empty: call_empty,
    Intf.get: call_get,
    Intf.get_nb: call_get_nb,
    cast: call_cast,
    signed: call_signed,
    QueueMeta.sub: call_sub,
    OutSig.write: outsig_write
}
