import inspect
import sys
from copy import copy

from pygears.conf import bind, core_log, registry, safe_bind, MultiAlternativeError, config
from pygears.typing import Any, cast, is_type
from pygears.core.util import is_standard_func, get_function_context_dict
from pygears.core.graph import get_source_producer, get_sim_cls_parent

from .partial import Partial
from .intf import Intf
from .infer_ftypes import TypeMatchError, infer_ftypes, type_is_specified
from .gear import TooManyArguments, GearTypeNotSpecified, GearArgsNotSpecified
from .gear import Gear, create_hier
from .gear_decorator import GearDecoratorPlugin
from .gear_memoize import get_memoized_gear, memoize_gear
from .port import HDLConsumer, HDLProducer, InPort, OutPort


def get_obj_var_name(frame, obj):
    for var_name, var_obj in frame.f_locals.items():
        if obj is var_obj:
            return var_name
    else:
        None


def find_current_gear_frame():
    import inspect
    code_map = registry('gear/code_map')
    if not code_map:
        return None

    for frame, *_ in inspect.stack():
        if frame.f_code is code_map[-1].func.__code__:
            return frame
    else:
        return None


def check_args_num(argnames, varargsname, args):
    if (len(args) < len(argnames)) or (not varargsname and
                                       (len(args) > len(argnames))):
        balance = "few" if (len(args) < len(argnames)) else "many"

        raise TooManyArguments(f"Too {balance} arguments provided.")


def check_args_specified(args):
    for name, intf in args.items():
        if not isinstance(intf, Intf):
            raise GearArgsNotSpecified(
                f'Unresolved input argument "{name}": {repr(intf)}')

        if not type_is_specified(intf.dtype):
            raise GearArgsNotSpecified(
                f'Input argument "{name}" has unresolved type "{repr(intf.dtype)}"'
            )


def resolve_gear_name(func, __base__):
    if __base__ is None:
        name = func.__name__
    else:
        name = __base__.__name__

    return name


def infer_const_args(args):
    args_res = {}
    const_args = {}
    for name, intf in args.items():
        if not isinstance(intf, Intf):
            from pygears.lib.const import get_literal_type
            try:
                const_args[name] = intf
                intf = Intf(get_literal_type(intf))
            except GearTypeNotSpecified:
                if isinstance(intf, Partial):
                    # raise GearArgsNotSpecified(
                    #     f'Unresolved gear "{intf.func.__name__}" with inputs'
                    #     f' {intf.args} and parameters {intf.kwds},'
                    #     f'connected to the input "{name}"')
                    raise GearArgsNotSpecified(
                        f'Unresolved gear "{intf.func.__name__}" with'
                        f' arguments {intf.args} and parameters {intf.kwds},'
                        f' connected to the input "{name}": {str(MultiAlternativeError(intf.errors))}'
                    )
                else:
                    raise GearArgsNotSpecified(
                        f'Unresolved argument "{intf}" connected to the input'
                        f' "{name}"')

        args_res[name] = intf

    return args_res, const_args


def expand_varargs(args, annotations, varargsname, varargs):
    vararg_type_list = []
    if varargsname in annotations:
        vararg_type = annotations[varargsname]
    else:
        vararg_type = Any

    # Append the types of the varargsname
    for i, a in enumerate(varargs):
        if isinstance(vararg_type, str):
            # If vararg_type is a template string, it can be made
            # dependent on the arguments position
            type_tmpl_i = vararg_type.format(i).encode()
        else:
            # Vararg is not a template and should be passed as is
            type_tmpl_i = vararg_type

        argname = f'{varargsname}{i}'

        vararg_type_list.append(argname)
        annotations[argname] = type_tmpl_i
        args[argname] = a

    if vararg_type_list:
        annotations[varargsname] = f'({", ".join(vararg_type_list)}, )'.encode(
        )


def resolve_return_annotation(annotations):
    if "return" in annotations:
        ret_anot = annotations["return"]
        if isinstance(ret_anot, dict):
            annotations['return'] = tuple(ret_anot.values())
            if len(annotations['return']) == 1:
                annotations['return'] = annotations['return'][0]
    else:
        annotations['return'] = None


def resolve_args(args, argnames, annotations, varargs):
    check_args_num(argnames, varargs, args)
    args_dict = {name: a for name, a in zip(argnames, args)}

    if varargs:
        expand_varargs(args_dict, annotations, varargs, args[len(args_dict):])

    resolve_return_annotation(annotations)

    for a in args_dict:
        if a not in annotations:
            annotations[a] = Any

    return args_dict, annotations


def gear_signature(func, args, kwds, meta_kwds):
    paramspec = inspect.getfullargspec(func)

    args, annotations = resolve_args(args, paramspec.args,
                                     paramspec.annotations, paramspec.varargs)

    kwddefaults = paramspec.kwonlydefaults or {}

    templates = {
        **annotations,
        **kwddefaults,
        **kwds,
        '_enablement': meta_kwds['enablement'],
    }

    return args, templates


def infer_params(args, params, context):
    arg_types = {name: arg.dtype for name, arg in args.items()}

    return infer_ftypes(params, arg_types, namespace=context)


def infer_outnames(annotations, meta_kwds):
    outnames = None
    if "return" in annotations:
        if isinstance(annotations['return'], dict):
            outnames = tuple(annotations['return'].keys())

    if not outnames:
        outnames = copy(meta_kwds['outnames'])

    if not outnames:
        outnames = []

    return outnames


class intf_name_tracer:
    def __init__(self, gear):
        self.code_map = registry('gear/code_map')
        self.gear = gear

    def tracer(self, frame, event, arg):
        if event == 'return':
            for cm in self.code_map:
                if frame.f_code is cm.func.__code__:
                    cm.func_locals = frame.f_locals.copy()

    def __enter__(self):
        self.code_map.append(self.gear)

        # tracer is activated on next call, return or exception
        if registry('gear/current_module').parent == registry(
                'gear/hier_root'):
            sys.setprofile(self.tracer)

        return self

    def __exit__(self, exception_type, exception_value, traceback):
        if registry('gear/current_module').parent == registry(
                'gear/hier_root'):
            sys.setprofile(None)

        cm = self.code_map.pop()

        if exception_type is None and hasattr(cm, 'func_locals'):
            for name, val in filter(lambda x: isinstance(x[1], Intf),
                                    cm.func_locals.items()):
                if not hasattr(val, 'var_name'):
                    val.var_name = name


def resolve_func(gear_inst):
    out_dtype = gear_inst.params.get('return', None)
    if out_dtype is None:
        out_dtype = ()
    elif out_dtype:
        if isinstance(out_dtype, dict):
            out_dtype = tuple(out_dtype.values())

    if not isinstance(out_dtype, tuple):
        out_dtype = (out_dtype, )

    if not is_standard_func(gear_inst.func):
        return tuple(), out_dtype

    with create_hier(gear_inst):
        # TODO: Try to detect infinite recursions
        # TODO: If the gear is instantiated in REPL, intf_name_tracer will fail
        with intf_name_tracer(gear_inst):
            out_intfs = gear_inst.func(*gear_inst.in_port_intfs,
                                       **gear_inst.explicit_params)

        if out_intfs is None:
            out_intfs = tuple()
        elif not isinstance(out_intfs, tuple):
            out_intfs = (out_intfs, )

        for i, intf in enumerate(out_intfs):
            if isinstance(intf, Partial):
                raise GearArgsNotSpecified(
                    f'Unresolved gear "{intf.func.__name__}" with'
                    f' arguments {intf.args} and parameters {intf.kwds},'
                    f' returned as output "{i}": {str(MultiAlternativeError(intf.errors))}'
                )

        err = None
        try:
            out_intfs, out_dtype = resolve_out_types(out_intfs, out_dtype,
                                                     gear_inst)
        except (TypeError, TypeMatchError) as e:
            err = type(e)(f"{str(e)}, when instantiating '{gear_inst.name}'")

        if err:
            raise err

    return out_intfs, out_dtype


def report_dangling(intf, gear_inst, p):
    if hasattr(intf, 'var_name'):
        core_log().warning(
            f'Interface "{gear_inst.name}/{intf.var_name}" left dangling.')
    else:
        path = []
        while True:
            g = p.gear

            if hasattr(p.consumer, 'var_name'):
                path.append(f'{g.parent.name}/{p.consumer.var_name}')
            else:
                path.append(p.name)

            if len(g.in_ports) != 1 or len(g.out_ports) != 1:
                break

            p = g.in_ports[0].producer.producer

        path = ' -> '.join(reversed(path))

        core_log().warning(f'Interface "{path}" left dangling.')


def resolve_gear(gear_inst, out_intfs, out_dtype, fix_intfs):
    dflt_dout_name = registry('gear/naming/default_out_name')
    for i in range(len(gear_inst.outnames), len(out_dtype)):
        if out_intfs and hasattr(out_intfs[i], 'var_name'):
            gear_inst.outnames.append(out_intfs[i].var_name)
        else:
            gear_inst.outnames.append(dflt_dout_name if len(out_dtype) ==
                                      1 else f'{dflt_dout_name}{i}')

    gear_inst.connect_output(out_intfs, out_dtype)

    # Connect output interfaces
    intfs = []
    out_intfs = []
    if isinstance(fix_intfs, dict):
        for i, (name, dt) in enumerate(zip(gear_inst.outnames, out_dtype)):
            if name in fix_intfs:
                intf = fix_intfs[name]
            else:
                intf = Intf(dt)
                out_intfs.append(intf)

            intfs.append(intf)

    elif fix_intfs:
        intfs = fix_intfs
    else:
        intfs = [Intf(dt) for dt in out_dtype]
        out_intfs = intfs

    assert len(intfs) == len(gear_inst.out_port_intfs)
    for intf, port in zip(intfs, gear_inst.out_ports):
        intf.source(port)

    if any(not type_is_specified(i.dtype) for i in out_intfs):
        raise GearTypeNotSpecified(
            f'Output type of the gear "{gear_inst.name}"'
            f' could not be resolved, and resulted in "{repr(out_dtype)}"')

    for c in gear_inst.child:
        channel_interfaces(c)
        for p in c.out_ports:
            intf = p.consumer
            if intf not in set(intfs) and intf not in set(
                    c.params['intfs']) and not intf.consumers:
                report_dangling(intf, gear_inst, p)

    if len(out_intfs) > 1:
        return tuple(out_intfs)
    elif len(out_intfs) == 1:
        return out_intfs[0]
    else:
        return None


def resolve_out_types(out_intfs, out_dtype, gear_inst):

    if out_intfs and (not out_dtype):
        out_dtype = tuple(intf.dtype for intf in out_intfs)
        return out_intfs, out_dtype

    if out_intfs:
        if len(out_intfs) != len(out_dtype):
            relation = 'smaller' if len(out_intfs) < len(
                out_dtype) else 'larger'
            raise TypeMatchError(
                f"Number of actual output interfaces ({len(out_intfs)}) is {relation} "
                f"than the number of specified output types: ({tuple(i.dtype for i in out_intfs)}) vs {repr(out_dtype)}"
            )

        casted_out_intfs = list(out_intfs)

        # Try casting interface types upfront to get better error messaging
        for i, (intf, t) in enumerate(zip(out_intfs, out_dtype)):
            err = None
            try:
                if intf.dtype != t:
                    cast(intf.dtype, t)
            except (TypeError, TypeMatchError) as e:
                err = type(e)(
                    f"{str(e)}, when casting type for output port {i}, "
                    f"when instantiating '{gear_inst.name}'")

            if err:
                raise err

        # If no exceptions occured, do it for real
        for i, (intf, t) in enumerate(zip(out_intfs, out_dtype)):
            if intf.dtype != t:
                from pygears.lib.cast import cast as cast_gear
                casted_out_intfs[i] = cast_gear(intf, t=t)

        out_intfs = tuple(casted_out_intfs)
        return out_intfs, out_dtype

    return out_intfs, out_dtype


def connect_to_existing_parent_in_port(sim_cls_parent, in_port):
    in_intf = in_port.producer
    src_intf = get_source_producer(in_port)

    for parent_port in sim_cls_parent.in_ports:
        if src_intf is get_source_producer(parent_port):
            in_intf.disconnect(in_port)
            parent_port.consumer.connect(in_port)
            return True

    return False


def connect_to_existing_parent_out_port(sim_cls_parent, out_port, cons_port):
    out_intf = out_port.consumer

    for parent_port in sim_cls_parent.out_ports:
        if out_intf is parent_port.consumer:
            out_intf.disconnect(cons_port)
            parent_port.consumer.connect(cons_port)
            return True

    return False


def channel_out_port(gear_inst, out_port, sim_cls_parent):
    out_parent_cons = []

    out_intf = out_port.consumer
    for cons_port in out_intf.consumers:
        cons_gear = cons_port.gear

        if sim_cls_parent.has_descendent(cons_gear):
            continue

        if connect_to_existing_parent_out_port(gear_inst.parent, out_port,
                                               cons_port):
            continue

        out_parent_cons.append(cons_port)

    if not out_parent_cons:
        return

    basename = getattr(out_intf, 'var_name', out_port.basename)
    parent_port = OutPort(gear_inst.parent, len(gear_inst.parent.out_ports),
                          basename)

    gear_inst.parent.out_ports.append(parent_port)

    in_intf = Intf(out_intf.dtype)
    out_intf.source(parent_port)
    in_intf.source(out_port)
    in_intf.connect(parent_port)

    for p in out_intf.consumers:
        if p in out_parent_cons:
            continue

        out_intf.disconnect(p)
        in_intf.connect(p)


def channel_in_port(gear_inst, in_port, sim_cls_parent):
    in_intf = in_port.producer
    prod_gear = in_intf.producer.gear

    if sim_cls_parent.has_descendent(prod_gear):
        return

    if connect_to_existing_parent_in_port(gear_inst.parent, in_port):
        return

    basename = getattr(in_intf, 'var_name', in_port.basename)
    parent_port = InPort(gear_inst.parent, len(gear_inst.parent.in_ports),
                         basename)

    gear_inst.parent.in_ports.append(parent_port)

    in_intf.disconnect(in_port)
    in_intf.connect(parent_port)

    gear_in_intf = Intf(in_intf.dtype)
    gear_in_intf.source(parent_port)
    gear_in_intf.connect(in_port)


def channel_interfaces(gear_inst):
    # if gear_inst.name == '/top_hier/hier':
    #     breakpoint()

    sim_cls_parent = get_sim_cls_parent(gear_inst)
    if sim_cls_parent is None:
        return

    for in_port in gear_inst.in_ports:
        channel_in_port(gear_inst, in_port, sim_cls_parent)

    for out_port in gear_inst.out_ports:
        channel_out_port(gear_inst, out_port, sim_cls_parent)


def terminate_internal_intfs(gear_inst):
    if not is_standard_func(gear_inst.func):
        for i in gear_inst.in_port_intfs:
            i.connect(HDLConsumer())

        for i in gear_inst.out_port_intfs:
            i.source(HDLProducer())


def gear_base_resolver(func,
                       meta_kwds,
                       *args,
                       name=None,
                       intfs=None,
                       __base__=None,
                       **kwds):

    name = name or resolve_gear_name(func, __base__)
    # if name == 'demux':
    #     breakpoint()

    err = None
    try:
        args, param_templates = gear_signature(func, args, kwds, meta_kwds)

        args, const_args = infer_const_args(args)
        check_args_specified(args)
    except (TooManyArguments, GearArgsNotSpecified) as e:
        err = type(e)(f'{str(e)}\n    when instantiating "{name}"')

    if err:
        raise err

    if intfs is None:
        fix_intfs = []
    elif isinstance(intfs, Intf):
        fix_intfs = [intfs]
    else:
        fix_intfs = intfs.copy()

    if config['gear/memoize']:
        gear_inst = get_memoized_gear(func, args, const_args, {
            **kwds,
            **meta_kwds
        }, fix_intfs, name)
        if gear_inst is not None:
            out_intfs = gear_inst.outputs
            if len(out_intfs) == 1:
                return out_intfs[0]
            else:
                return out_intfs

    try:
        params = infer_params(args,
                              param_templates,
                              context=get_function_context_dict(func))
    except TypeMatchError as e:
        err = TypeMatchError(f'{str(e)}, of the module "{name}"')
        params = e.params
    except Exception as e:
        err = type(e)(f'{str(e)}, of the module "{name}"')

    if err and not isinstance(err, TypeMatchError):
        raise err

    if not err:
        if not params.pop('_enablement'):
            err = TypeMatchError(
                f'Enablement condition failed for "{name}" alternative'
                f' "{meta_kwds["definition"].__module__}.'
                f'{meta_kwds["definition"].__name__}": '
                f'{meta_kwds["enablement"].decode()}')

    params['outnames'] = infer_outnames(func.__annotations__, meta_kwds)

    for key in meta_kwds:
        if key not in ['outnames', 'enablement']:
            params[key] = meta_kwds[key]

    params['name'] = name
    params['intfs'] = fix_intfs
    gear_inst = Gear(func, params)

    if err:
        err.gear = gear_inst
        err.root_gear = gear_inst

    if not err:
        gear_inst.connect_input(args, const_args)
        try:
            out_intfs, out_dtype = resolve_func(gear_inst)
            out_intfs = resolve_gear(gear_inst, out_intfs, out_dtype,
                                     fix_intfs)
            terminate_internal_intfs(gear_inst)

        except (TooManyArguments, GearTypeNotSpecified, GearArgsNotSpecified,
                TypeError, TypeMatchError, MultiAlternativeError) as e:
            err = e
            if hasattr(func, 'alternatives') or hasattr(
                    func, 'alternative_to'):
                err.root_gear = gear_inst

    if err:
        if hasattr(func, 'alternatives') or hasattr(func, 'alternative_to'):
            gear_inst.parent.child.remove(gear_inst)
            for port in gear_inst.in_ports:
                if port.basename not in gear_inst.const_args:
                    port.producer.consumers.remove(port)
                else:
                    gear_inst.parent.child.remove(port.producer.producer.gear)

        raise err

    if config['gear/memoize'] and not func.__name__.endswith('_unpack'):
        memoize_gear(gear_inst, args, const_args, kwds)

    return out_intfs


class GearInstPlugin(GearDecoratorPlugin):
    @classmethod
    def bind(cls):
        safe_bind('gear/code_map', [])
        safe_bind('gear/gear_dflt_resolver', gear_base_resolver)
        config.define('gear/memoize', False)

    @classmethod
    def reset(cls):
        safe_bind('gear/code_map', [])
