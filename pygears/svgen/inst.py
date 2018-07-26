from pygears import PluginBase, registry
from pygears.core.hier_node import HierVisitorBase
from pygears.svgen.intf import SVIntfGen


class SVGenInstVisitor(HierVisitorBase):
    def __init__(self):
        self.namespace = registry('SVGenModuleNamespace')
        self.svgen_map = registry('SVGenMap')

    def RTLNode(self, node):
        svgen_cls = node.params['svgen']['svgen_cls']

        if svgen_cls:
            svgen_inst = svgen_cls(node)
        else:
            svgen_inst = None

        self.svgen_map[node] = svgen_inst

    def RTLIntf(self, intf):
        self.svgen_map[intf] = SVIntfGen(intf)


def svgen_inst(top, conf):
    # if 'outdir' in conf:
    #     registry('SVGenSystemVerilogPaths').append(conf['outdir'])

    v = SVGenInstVisitor()
    v.visit(top)

    return top


class SVGenInstPlugin(PluginBase):
    @classmethod
    def bind(cls):
        cls.registry['SVGenModuleNamespace'] = {}
        cls.registry['SVGenMap'] = {}
        cls.registry['SVGenSystemVerilogPaths'] = []

    @classmethod
    def reset(cls):
        cls.registry['SVGenMap'] = {}
