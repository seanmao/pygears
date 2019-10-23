from dataclasses import dataclass
from enum import IntEnum
from functools import partial, reduce

from .conditions_utils import (COND_TYPES, CombinedCond, CondBase, CycleCond,
                               ExitCond, InCond, StateCond, SubCond, combine)
from .hls_expressions import (BinOpExpr, CastExpr, ConcatExpr, UnaryOpExpr,
                              binary_expr)
from .inst_visit import InstanceVisitor, PydlFromCBlockVisitor
from .pydl_types import (Block, CycleSubCond, ExitSubCond, IfBlock, Module,
                         SubConditions, is_container, is_intftype)
from .scheduling_types import SeqCBlock
from .utils import VisitError


def find_conditions(schedule, state_num):
    '''Finds and assigns conditions to pydl blocks as a 'cond_val' attribute'''
    finder = ConditionsFinder(state_num)
    finder.visit(schedule)

    resolver = ConditionsResolve(finder.conds_by_id)
    resolver.visit(schedule)


@dataclass
class CondtitionValues:
    name = None
    in_val = None
    cycle_val = None
    exit_val = None


class BlockType(IntEnum):
    leaf = 0
    prolog = 1
    epilog = 2
    block = 3


class ComplexCondResolve:
    '''Visitor class that resolves complex condtitions (inherited from CondBase)'''

    def visit(self, node, conds_by_id):
        if node is None or not isinstance(node, CondBase):
            return node

        if node.ctype in conds_by_id:
            return conds_by_id[node.ctype][node.id]

        method = 'visit_' + node.ctype
        visitor = getattr(self, method)
        return visitor(node, conds_by_id)

    def get_sub_conds(self, cond, conds_by_id):
        return [self.visit(sub_cond, conds_by_id) for sub_cond in cond.id]

    def visit_combined(self, node, conds_by_id):
        sub_conds = self.get_sub_conds(node, conds_by_id)
        return reduce(partial(binary_expr, operator=node.operator), sub_conds,
                      None)

    def visit_state(self, node, conds_by_id):
        sub_conds = self.get_sub_conds(node, conds_by_id)
        return node.state_expr(sub_conds)

    def visit_expr(self, node, conds_by_id):
        expr = node.sub_expr
        if expr is None:
            return None

        if isinstance(expr, (ConcatExpr, BinOpExpr)):
            for i, op in enumerate(expr.operands):
                cond_val = self.visit(op, conds_by_id)
                expr.operands[i] = cond_val
            return expr

        if isinstance(expr, (UnaryOpExpr, CastExpr)):
            cond_val = self.visit(expr.operand, conds_by_id)
            expr.operand = cond_val
            return expr

        raise VisitError('Unknown sub_expr in ExprCond')

    def visit_sub(self, node, conds_by_id):
        cond = self.visit(node.cond, conds_by_id)
        other = self.visit(node.other, conds_by_id)
        if other is None:
            if node.operator == '&&':
                return cond
            else:
                return None

        return reduce(partial(binary_expr, operator=node.operator),
                      [cond, other], None)


def find_complex_cond(cond_inst, conds_by_id):
    return ComplexCondResolve().visit(cond_inst, conds_by_id)


def resolve_complex_cond(pydl_block, conds_by_id):
    for cond_t in COND_TYPES:
        propagated_inst = getattr(pydl_block.cond_val, f'{cond_t}_val', None)
        resolved_inst = find_complex_cond(propagated_inst, conds_by_id)
        setattr(pydl_block.cond_val, f'{cond_t}_val', resolved_inst)


def create_state_cycle_cond(child):
    child_cond = CycleCond(child.pydl_block.id)
    return StateCond(id=[child_cond], state_ids=child.state_ids)


class ConditionsResolve(PydlFromCBlockVisitor):
    '''Visitor class for resolving pydl blocks and stmt conditions.
    Takes the conds_by_id and resolves complex types.
    Takes the pydl block structure and resolves all 'cond_val'
    '''

    def __init__(self, conds_by_id):
        self.conds_by_id = conds_by_id
        self.resolve_initial()
        self.cond_cnt = 0

    def resolve_initial(self):
        assert len(self.conds_by_id['in']) == len(
            self.conds_by_id['cycle']) == len(self.conds_by_id['exit'])

        block_num = len(self.conds_by_id['in'])

        for i in reversed(range(block_num)):
            for cond_t in COND_TYPES:
                curr_cond = self.conds_by_id[cond_t][i]
                new_cond = find_complex_cond(curr_cond, self.conds_by_id)
                self.conds_by_id[cond_t][i] = new_cond

    def set_name(self, node):
        node.cond_val.name = self.cond_cnt
        self.cond_cnt += 1

    def visit_all_Block(self, node):
        resolve_complex_cond(node, self.conds_by_id)
        self.set_name(node)

    def visit_all_Expr(self, node):
        resolve_complex_cond(node, self.conds_by_id)
        self.set_name(node)


class ConditionsFinder(InstanceVisitor):
    '''Visitor class for finding pydl block conditions.
    Evaluated in two manners:
       - looks at top scope for cblock context and state transtions
       - looks at bottom hier. for sub conditons
    As a result a 'cond_val' attribute (CondtitionValues) is added to
    each pydl block or stmt and self.conds_by_id holds all found conditions
    '''

    def __init__(self, state_num):
        self.state_num = state_num
        self.scope = []

        self.conds_by_id = {'in': {}, 'cycle': {}, 'exit': {}}

    def enter_block(self, block):
        self.scope.append(block)

    def exit_block(self):
        self.scope.pop()

    def visit_block(self, node):
        if node.prolog:
            prolog_cond = find_top_context_conditions(self.scope,
                                                      BlockType.prolog,
                                                      self.state_num)
            for block in node.prolog:
                self.set_pydl_cond(block, prolog_cond, node.parent)

        self.enter_block(node)

        curr_cond = find_top_context_conditions(self.scope, BlockType.block,
                                                self.state_num)
        self.eval_pydl_cond(node.pydl_block, curr_cond, node)

        for child in node.child:
            self.visit(child)

        added_epilog_cond = find_top_context_rst_cond(
            self.scope) if node.epilog else None

        self.exit_block()

        if node.epilog:
            epilog_cond = find_top_context_conditions(self.scope,
                                                      BlockType.epilog,
                                                      self.state_num,
                                                      added_epilog_cond)
            for block in node.epilog:
                self.set_pydl_cond(block, epilog_cond, node.parent)
                block.cond_val.in_val = CombinedCond(
                    (curr_cond.exit_val, block.cond_val.in_val), '&&')

    def visit_SeqCBlock(self, node):
        self.visit_block(node)

    def visit_MutexCBlock(self, node):
        self.visit_block(node)

    def visit_Leaf(self, node):
        curr_cond = find_top_context_conditions(self.scope, BlockType.leaf,
                                                self.state_num)
        for pydl_block in node.pydl_blocks:
            self.set_pydl_cond(pydl_block, curr_cond, node.parent)

    def set_pydl_cond(self, pydl_block, curr_cond, cblock):
        if isinstance(pydl_block, Block):
            self.eval_pydl_cond(pydl_block, curr_cond, cblock)

            for stmt in pydl_block.stmts:
                self.set_pydl_cond(stmt, curr_cond, cblock)
        else:
            val = CondtitionValues()
            for cond_t in COND_TYPES:
                curr_inst = getattr(curr_cond, f'{cond_t}_val', None)
                setattr(val, f'{cond_t}_val', curr_inst)
            pydl_block.cond_val = val

    def eval_pydl_cond(self, pydl_block, curr_cond, cblock):
        pydl_block.cond_val = CondtitionValues()
        bottom_cond = find_bottom_context_conditions(pydl_block, cblock)

        for cond_t in COND_TYPES:
            current_cond_inst = getattr(bottom_cond, f'{cond_t}_val', None)
            self.conds_by_id[cond_t][pydl_block.id] = current_cond_inst
            propagated_inst = getattr(curr_cond, f'{cond_t}_val', None)
            if cond_t == 'in':
                curr_in_cond = InCond(pydl_block.id)
                if propagated_inst is not None:
                    propagated_inst = CombinedCond(
                        (propagated_inst, curr_in_cond))
                else:
                    propagated_inst = curr_in_cond

            setattr(pydl_block.cond_val, f'{cond_t}_val', propagated_inst)


################################################
# Top cblocks specify several context conditions
################################################


def find_top_context_conditions(scope, block_type, state_num, added_cond=None):
    curr_cond = CondtitionValues()

    in_cond = find_top_context_in_cond(scope, block_type, state_num)
    cycle_cond = find_top_context_cycle_cond(scope, block_type, state_num)
    exit_cond = find_top_context_exit_cond(scope, block_type, state_num)

    curr_cond.in_val = in_cond
    if added_cond is None:
        curr_cond.cycle_val = cycle_cond
        curr_cond.exit_val = exit_cond
    else:
        curr_cond.cycle_val = combine((cycle_cond, added_cond))
        curr_cond.exit_val = combine((exit_cond, added_cond))

    return curr_cond


def find_top_context_cycle_cond(scope, block_type, state_num):
    cond = []
    for c_block in reversed(scope[1:]):
        # state changes break the cycle
        if len(c_block.state_ids) > len(scope[-1].state_ids):
            break

        if block_type != BlockType.block and len(c_block.state_ids) > 1:
            return state_depend_cycle_cond(scope, block_type)

        block = c_block.pydl_block
        if is_container(block):
            continue

        if block.cycle_cond and block.cycle_cond != 1:
            cond.append(CycleCond(block.id))

        if hasattr(block, 'multicycle') and block.multicycle:
            break

    return combine(cond)


def find_top_context_exit_cond(scope, block_type, state_num):
    return ExitCond(scope[-1].pydl_block.id)


def find_top_context_in_cond(scope, block_type, state_num):
    cblock = scope[-1]

    if state_num == 0:
        return None
    if not cblock.parent:
        return None
    if block_type == BlockType.leaf:
        return None

    current_ids = cblock.state_ids
    # if in module even exist states other than the ones in this
    # cblock
    if (current_ids != cblock.parent.state_ids) and (current_ids != list(
            range(state_num + 1))):
        return StateCond(id=[], state_ids=current_ids)

    return None


def find_top_context_rst_cond(scope):
    if len(scope) == 1:
        assert isinstance(scope[0].pydl_block, Module)
        block = scope[0].pydl_block.stmts
    else:
        block = [s.pydl_block for s in scope[1:]]
    return find_exit_cond(block, search_in_cond=True)


def find_exit_cond(statements, search_in_cond=False):
    def has_in_cond(stmt):
        if search_in_cond and (not isinstance(stmt, IfBlock)) and hasattr(
                stmt, 'in_cond') and (stmt.in_cond is not None):
            return True
        return False

    for stmt in reversed(statements):
        cond = getattr(stmt, 'exit_cond', None)
        if cond is not None:
            exit_c = ExitCond(stmt.id)
            if has_in_cond(stmt):
                in_c = InCond(stmt.id)
                return CombinedCond((exit_c, in_c), '&&')

            return exit_c

        if has_in_cond(stmt):
            return InCond(stmt.id)

    return None


def state_depend_cycle_cond(scope, block_type):
    c_block = scope[-1]

    if block_type == BlockType.prolog:
        return create_state_cycle_cond(c_block.child[0])

    if block_type == BlockType.epilog:
        return create_state_cycle_cond(c_block.child[-1])

    raise Exception('State dependency but prolog/epilog in cycle cond')


#########################
# Bottom hier. evaluation
#########################


def find_bottom_context_conditions(pydl_block, scope):
    curr_cond = CondtitionValues()

    curr_cond.in_val = find_bottom_context_in_cond(pydl_block, scope)
    curr_cond.cycle_val = find_bottom_context_cycle_cond(pydl_block, scope)
    curr_cond.exit_val = find_bottom_context_exit_cond(pydl_block, scope)

    return curr_cond


def find_bottom_context_in_cond(block, scope):
    return block.in_cond


def find_bottom_context_cycle_cond(block, scope):
    if block != scope.pydl_block:
        # leaf
        return pydl_cycle_subconds(block)

    if is_container(block):
        return merge_cblock_conds(scope, 'cycle')

    curr_cond = block.cycle_cond
    if not isinstance(curr_cond, SubConditions):
        return curr_cond

    return cblock_cycle_subconds(curr_cond, scope)


def find_bottom_context_exit_cond(block, scope):
    if block != scope.pydl_block:
        # leaf
        return pydl_exit_subconds(block)

    if is_container(block):
        return merge_cblock_conds(scope, 'exit')

    curr_cond = block.exit_cond
    if not isinstance(curr_cond, SubConditions):
        return curr_cond

    return cblock_exit_subconds(curr_cond, scope)


###############################
# Subconditions for pydl blocks
###############################


def pydl_subconds(block, cond_type):
    if cond_type == 'cycle':
        return pydl_cycle_subconds(block)
    return pydl_exit_subconds(block)


def pydl_cycle_subconds(block):
    if is_container(block):
        return merge_pydl_conds(block, 'cycle')

    cond = getattr(block, 'cycle_cond', None)
    if isinstance(cond, SubConditions):
        return pydl_block_subcond_expr(cond, block)
    return cond


def pydl_exit_subconds(block):
    if is_container(block):
        return merge_pydl_conds(block, 'exit')

    cond = getattr(block, 'exit_cond', None)
    if isinstance(cond, SubConditions):
        return pydl_block_subcond_expr(cond, block)
    return cond


def pydl_block_subcond_expr(cond, block):
    if isinstance(cond, CycleSubCond):
        sub_c = pydl_stmt_cycle_cond(block)
    elif isinstance(cond, ExitSubCond):
        sub_c = pydl_stmt_exit_cond(block)
    else:
        sub_c = CombinedCond(
            (pydl_stmt_cycle_cond(block), pydl_stmt_exit_cond(block)),
            cond.operator)

    return subcond_expr(cond, sub_c)


def subcond_expr(cond, other=None):
    if other is None:
        return None

    if cond.expr is not None:
        return SubCond(cond=cond.expr, other=other, operator=cond.operator)

    return other


def pydl_stmt_cycle_cond(block):
    conds = []
    for stmt in block.stmts:
        sub_cond = pydl_cycle_subconds(stmt)
        if sub_cond is not None:
            conds.append(CycleCond(stmt.id))
    return combine(conds)


def pydl_stmt_exit_cond(block):
    for stmt in reversed(block.stmts):
        exit_c = pydl_exit_subconds(stmt)
        if exit_c is not None:
            return exit_c
    return None


##################################
# Subconditions for control blocks
##################################


def cblock_subconds(cond, cblock, cond_type):
    if cond_type == 'cycle':
        return cblock_cycle_subconds(cond, cblock)

    return cblock_exit_subconds(cond, cblock)


def cblock_cycle_subconds(cond, cblock):
    if isinstance(cblock, SeqCBlock) and len(cblock.state_ids) > 1:
        sub_conds = cblock_state_cycle_subconds(cblock)
    else:
        sub_conds = cblock_simple_cycle_subconds(cblock)

    return subcond_expr(cond, sub_conds)


def cblock_exit_subconds(cond, cblock):
    if isinstance(cblock, SeqCBlock) and len(cblock.state_ids) > 1:
        sub_conds = cblock_state_exit_subconds(cblock)
    else:
        sub_conds = cblock_simple_exit_subconds(cblock)
    return subcond_expr(cond, sub_conds)


# Subconditons expanded


def cblock_simple_cycle_subconds(cblock):
    conds = []
    for child in get_cblock_child(cblock):
        for pydl_stmt in get_cblock_pydl_stmts(child):
            if getattr(pydl_stmt, 'cycle_cond', None) is not None:
                conds.append(CycleCond(pydl_stmt.id))

    return combine(conds)


def cblock_state_cycle_subconds(cblock):
    curr_child = cblock.child[-1]
    sub_conds = curr_child.pydl_block.cycle_cond
    if sub_conds is not None:
        sub_conds = StateCond(state_ids=curr_child.state_ids,
                              id=[CycleCond(curr_child.pydl_block.id)])

    return sub_conds


def cblock_state_exit_subconds(cblock):
    curr_child = cblock.child[-1]
    sub_conds = curr_child.pydl_block.exit_cond
    if sub_conds is not None:
        sub_conds = StateCond(state_ids=curr_child.state_ids,
                              id=[ExitCond(curr_child.pydl_block.id)])

    return sub_conds


def cblock_simple_exit_subconds(cblock):
    exit_c = None
    children = [x for x in get_cblock_child(cblock)]
    for child in reversed(children):
        pydl_stmts = [x for x in get_cblock_pydl_stmts(child)]
        for pydl_stmt in reversed(pydl_stmts):
            if is_container(pydl_stmt):
                child_exit_cond = merge_pydl_conds(pydl_stmt, 'exit')
            else:
                child_exit_cond = getattr(pydl_stmt, 'exit_cond', None)
            if child_exit_cond is not None:
                exit_c = ExitCond(pydl_stmt.id)
                if is_intftype(pydl_stmt) and pydl_stmt.in_cond is not None:
                    exit_c = CombinedCond((exit_c, InCond(pydl_stmt.id)))
                return exit_c
    return 1


#######################################################
# Container blocks need to have their conditions merged
#######################################################


def merge_cblock_conds(top, cond_type):
    cblocks = [x for x in get_cblock_child(top)]

    if all([
            getattr(pydl_stmt, f'{cond_type}_cond', None) is None
            for child in cblocks for pydl_stmt in get_cblock_pydl_stmts(child)
    ]):
        return None

    cond = None
    for child in cblocks:
        for curr_block in get_cblock_pydl_stmts(child):
            sub_cond = getattr(curr_block, f'{cond_type}_cond', None)
            if sub_cond is not None:
                sub_cond = cblock_subconds(sub_cond, child, cond_type)
            cond = combine_block_cond(sub_cond, curr_block, cond)
    return cond


def merge_pydl_conds(top, cond_type):
    if all([
            getattr(stmt, f'{cond_type}_cond', None) is None
            for stmt in top.stmts
    ]):
        return None

    cond = None
    for stmt in top.stmts:
        sub_cond = None
        if getattr(stmt, f'{cond_type}_cond', None):
            sub_cond = pydl_subconds(stmt, cond_type)
        cond = combine_block_cond(sub_cond, stmt, cond)
    return cond


#######################################################


def combine_block_cond(sub_cond, stmt, cond):
    if getattr(stmt, 'in_cond', None) is None:
        block_cond = sub_cond
    else:
        if sub_cond is None:
            block_cond = None
        else:
            block_cond = combine((sub_cond, InCond(stmt.id)))

    if cond is None:
        return block_cond

    if block_cond is None:
        return cond

    return combine((cond, block_cond), '||')


def get_cblock_child(cblock):
    if hasattr(cblock, 'child'):
        yield from cblock.child
    else:
        yield cblock  # Leaf


def get_cblock_pydl_stmts(cblock):
    if hasattr(cblock, 'pydl_block'):
        yield cblock.pydl_block
    else:
        yield from cblock.pydl_blocks