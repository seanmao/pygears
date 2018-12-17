import typing as pytypes

from pygears.typing import Tuple, typeof, Uint, Queue, is_type

bin_operators = ['!', '==', '>', '>=', '<', '<=', '!=', '&&', '||']
extendable_operators = [
    '+', '-', '*', '/', '%', '**', '<<', '>>>', '|', '&', '^', '/', '~', '!'
]


def find_exit_cond(statements):
    cond = []
    for stmt in statements:
        if getattr(stmt, 'exit_cond', None):
            cond.extend(stmt.exit_cond)
    return cond


def find_cycle_cond(statements):
    cond = []
    for stmt in statements:
        if hasattr(stmt, 'cycle_cond') and not stmt.in_cond:
            cond.extend(stmt.cycle_cond)
        else:
            # TODO
            if isinstance(stmt, Yield):
                cond.append(stmt)
    return cond


# Expressions


class Expr:
    @property
    def dtype(self):
        pass


class ResExpr(Expr, pytypes.NamedTuple):
    val: pytypes.Any

    @property
    def dtype(self):
        return type(self.val)


class RegDef(Expr, pytypes.NamedTuple):
    val: pytypes.Any
    name: str

    @property
    def dtype(self):
        if is_type(type(self.val)):
            return type(self.val)
        else:
            return self.val.dtype


class RegNextExpr(Expr, pytypes.NamedTuple):
    reg: RegDef
    val: Expr

    @property
    def dtype(self):
        return self.reg.dtype


class RegVal(Expr, pytypes.NamedTuple):
    reg: RegDef
    name: str

    @property
    def dtype(self):
        return self.reg.dtype


class VariableDef(pytypes.NamedTuple):
    val: pytypes.Any
    name: str

    @property
    def dtype(self):
        return self.val.dtype


class VariableExpr(Expr, pytypes.NamedTuple):
    variable: VariableDef
    val: Expr

    @property
    def dtype(self):
        return self.variable.dtype


class VariableVal(Expr, pytypes.NamedTuple):
    variable: VariableDef
    name: str

    @property
    def dtype(self):
        return self.variable.dtype


class IntfExpr(Expr, pytypes.NamedTuple):
    intf: pytypes.Any
    context: str = None

    @property
    def name(self):
        return self.intf.basename

    @property
    def dtype(self):
        return self.intf.dtype


class ConcatExpr(Expr, pytypes.NamedTuple):
    operands: tuple

    @property
    def dtype(self):
        return Tuple[tuple(op.dtype for op in self.operands)]


class UnaryOpExpr(Expr, pytypes.NamedTuple):
    operand: Expr
    operator: str

    @property
    def dtype(self):
        return Uint[1] if (self.operand is '!') else self.operand.dtype


class BinOpExpr(Expr, pytypes.NamedTuple):
    operands: tuple
    operator: str

    @property
    def dtype(self):
        if self.operator in bin_operators:
            return Uint[1]

        t = eval(f'op1 {self.operator} op2', {
            'op1': self.operands[0].dtype,
            'op2': self.operands[1].dtype
        })
        if isinstance(t, bool):
            return Uint[1]
        else:
            return t


class ArrayOpExpr(Expr, pytypes.NamedTuple):
    array: Expr
    operator: str

    @property
    def dtype(self):
        return Uint[1]


class SubscriptExpr(Expr, pytypes.NamedTuple):
    val: Expr
    index: pytypes.Any

    @property
    def dtype(self):
        if not isinstance(self.index, slice):
            return self.val.dtype[self.index]
        else:
            return self.val.dtype.__getitem__(self.index)


class AttrExpr(Expr, pytypes.NamedTuple):
    val: Expr
    attr: list

    @property
    def dtype(self):
        return self.get_attr_dtype(self.val.dtype)

    def get_attr_dtype(self, t):
        for attr in self.attr:
            if typeof(t, Tuple):
                t = t[attr]
            elif typeof(t, Queue):
                try:
                    t = t[attr]
                except KeyError:
                    t = self.get_attr_dtype(t[0])
            else:
                t = getattr(t, attr, None)
        return t


class Yield(pytypes.NamedTuple):
    expr: Expr


# Blocks


class Block:
    @property
    def in_cond(self):
        pass

    @property
    def cycle_cond(self):
        pass

    @property
    def exit_cond(self):
        pass


class IntfBlock(Block, pytypes.NamedTuple):
    intf: pytypes.Any
    stmts: list

    @property
    def in_cond(self):
        return self.intf

    @property
    def cycle_cond(self):
        return find_cycle_cond(self.stmts)

    @property
    def exit_cond(self):
        return None


class IntfLoop(Block, pytypes.NamedTuple):
    intf: pytypes.Any
    stmts: list
    multicycle: list = None

    @property
    def in_cond(self):
        return self.intf

    @property
    def cycle_cond(self):
        return find_cycle_cond(self.stmts)

    @property
    def exit_cond(self):
        conds = self.cycle_cond
        conds.append(self.intf.intf)
        return conds + find_exit_cond(self.stmts)


class IfBlock(Block, pytypes.NamedTuple):
    in_cond: Expr
    stmts: list

    @property
    def cycle_cond(self):
        return find_cycle_cond(self.stmts)


class IfElseBlock(Block, pytypes.NamedTuple):
    in_cond: Expr
    if_block: Block
    else_block: Block

    @property
    def cycle_cond(self):
        return find_cycle_cond(self.stmts)


class Loop(Block, pytypes.NamedTuple):
    in_cond: Expr
    stmts: list
    exit_c: Expr
    multicycle: list = None

    @property
    def cycle_cond(self):
        return find_cycle_cond(self.stmts)

    @property
    def exit_cond(self):
        conds = self.cycle_cond
        if self.exit_c:
            conds.append(self.exit_c)
        return conds + find_exit_cond(self.stmts)


class Module(pytypes.NamedTuple):
    in_ports: pytypes.List
    out_ports: pytypes.List
    locals: pytypes.Dict
    regs: pytypes.Dict
    variables: pytypes.Dict
    stmts: pytypes.List


def isloop(block):
    return isinstance(block, Loop) or isinstance(block, IntfLoop)
