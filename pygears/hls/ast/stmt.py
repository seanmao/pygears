import ast
from . import Context, FuncContext, SyntaxError, node_visitor, ir, visit_ast
from .utils import add_to_list
from .cast import resolve_cast_func


class UnknownName(SyntaxError):
    pass


def infer_targets(ctx, target, dtype, obj_factory=None):
    if isinstance(target, ir.Name):
        if target.name not in ctx.scope:
            if obj_factory is None:
                breakpoint()
                raise NameError

            var = obj_factory(target.name, dtype)
            ctx.scope[target.name] = var
            target.obj = var
        # else:
        #     assert target.dtype == dtype
    elif isinstance(target, ir.ConcatExpr):
        for t, d in zip(target.operands, dtype):
            infer_targets(ctx, t, d, obj_factory)
    elif isinstance(target, ir.SubscriptExpr):
        # Todo can we do some check here?
        pass
    else:
        breakpoint()


def assign_targets(ctx, target, source, obj_factory=None):
    infer_targets(ctx, target, source.dtype, obj_factory)
    return ir.AssignValue(target, source)


@node_visitor(ast.AnnAssign)
def _(node, ctx: Context):
    targets = visit_ast(node.target, ctx)
    annotation = visit_ast(node.annotation, ctx)

    if node.value is None:
        ctx.scope[targets.name] = ir.Variable(targets.name, annotation.val)
        return

    if node.value:
        init = visit_ast(node.value, ctx)

        init_cast = ir.CastExpr(init, annotation.val)
        stmts = assign_targets(ctx, targets, init_cast, ir.Variable)
        if not isinstance(stmts, list):
            stmts = [stmts]

        for s in stmts:
            s.target.obj.val = s.val
            if init.val is None or getattr(init.val, 'unknown', False):
                s.target.obj.any_init = True


@node_visitor(ast.AugAssign)
def _(node, ctx: Context):
    target = visit_ast(node.target, ctx)
    value = visit_ast(node.value, ctx)
    return ir.AssignValue(
        target, ir.BinOpExpr((ctx.ref(target.name), value), type(node.op)))


@node_visitor(ast.Assign)
def _(node, ctx: Context):
    value = visit_ast(node.value, ctx)
    stmts = []
    for t in node.targets:
        targets = visit_ast(t, ctx)
        add_to_list(stmts, assign_targets(ctx, targets, value, ir.Variable))

    return stmts


@node_visitor(ast.Assert)
def _(node, ctx: Context):
    test = visit_ast(node.test, ctx)
    msg = node.msg.s if node.msg else 'Assertion failed.'
    return ir.Assert(test, msg=msg)


@node_visitor(ast.Return)
def _(node: ast.Return, ctx: FuncContext):
    expr = visit_ast(node.value, ctx)

    if not isinstance(ctx, FuncContext):
        raise Exception('Return found outside function')

    if ctx.ret_dtype is not None:
        expr = resolve_cast_func(expr, ctx.ret_dtype)
    else:
        ctx.ret_dtype = expr.dtype

    return ir.FuncReturn(ctx.funcref, expr)


@node_visitor(ast.Pass)
def _(node: ast.Pass, ctx: Context):
    return None