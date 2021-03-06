import inspect

from pygears import reg, Intf, module, find
from pygears.sim.sim_gear import SimGear, is_simgear_func
from pygears.sim.sim import SimPlugin, cosim
from pygears.core.gear import GearPlugin
from pygears.core.gear_inst import gear_base_resolver
from pygears.core.hier_node import HierVisitorBase
from pygears.core.port import HDLConsumer, HDLProducer


def sim_compile_resolver(func, *args, **kwds):
    ctx = reg['gear/exec_context']
    if ctx == 'sim':
        reg['gear/exec_context'] = 'compile'

        local_in = []
        for a in args:
            if isinstance(a, Intf):
                a.consumers.clear()
                local_in.append(a)
            else:
                from pygears.lib.const import get_literal_type
                local_in.append(Intf(get_literal_type(a)))
                local_in[-1].producer = HDLProducer()

        reg['gear/exec_context'] = 'sim'

        outputs = gear_base_resolver(func, *local_in, **kwds)

        # TODO: Support multiple outputs

        if isinstance(outputs, tuple):
            raise Exception("Not yet supported")

        outputs.connect(HDLConsumer())

        gear_inst = outputs.producer.gear

        def is_async_gen(func):
            return bool(func.__code__.co_flags & inspect.CO_ASYNC_GENERATOR)

        if not is_async_gen(gear_inst.func):
            raise Exception("Not yet supported")

        import asyncio
        for intf, a in zip(local_in, args):
            if isinstance(a, Intf):
                continue

            intf._in_queue = asyncio.Queue(maxsize=1, loop=reg['sim/simulator'])
            intf.put_nb(a)

        simulator = reg['sim/simulator']
        cur_sim = reg['gear/current_sim']
        sim_map = reg['sim/map']

        sim_gear = SimGear(gear_inst)
        sim_map[gear_inst] = sim_gear
        cur_sim.child.append(sim_gear)
        simulator.forward_ready.add(sim_gear)
        simulator.tasks[sim_gear] = sim_gear.run()
        simulator.task_data[sim_gear] = None

        return outputs
    else:
        return gear_base_resolver(func, *args, **kwds)


class SimInstVisitor(HierVisitorBase):
    def __init__(self):
        self.namespace = reg['sim/module_namespace']
        self.sim_map = reg['sim/map']

    def Gear(self, module):
        if module.parent is None:
            return False

        if module.params.get('__sim__', None) is not None:
            cfg = module.params['__sim__']
            if isinstance(cfg, dict):
                cosim(module, cfg['sim'])
            else:
                cosim(module, cfg)

        sim_cls = module.params.get('sim_cls', None)
        sim_inst = None

        if sim_cls is None:
            sim_cls = self.namespace.get(module.definition, None)

        if sim_cls:
            sim_inst = sim_cls(module)
        elif is_simgear_func(module.func):
            sim_inst = SimGear(module)

        if sim_inst:
            self.sim_map[module] = sim_inst
            return True


def sim_inst(top=None):
    if top is None:
        top = find('/')

    v = SimInstVisitor()
    v.visit(top)

    return top


class SimInstPlugin(SimPlugin, GearPlugin):
    @classmethod
    def bind(cls):
        reg['sim/flow'].append(sim_inst)
        reg['sim/module_namespace'] = {}
        reg['sim/map'] = {}
        reg['gear/params/extra/sim_cls'] = None
        reg['gear/params/extra/__sim__'] = None
        reg['gear/gear_dflt_resolver'] = sim_compile_resolver

    @classmethod
    def reset(cls):
        reg['sim/map'] = {}
