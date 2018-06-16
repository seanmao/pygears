class TypingVisitorBase:
    def visit(self, type_, field=None, **kwds):
        visit_func_name = f'visit_{type_.__name__.lower()}'

        visit_func = getattr(self, visit_func_name, self.visit_default)

        return visit_func(type_, field, **kwds)

    def visit_union(self, type_, field, **kwds):
        for t, f in zip(type_.types(), type_.fields):
            self.visit(t, f)

    def visit_int(self, type_, field, **kwds):
        pass

    def visit_bool(self, type_, field, **kwds):
        pass

    def visit_uint(self, type_, field, **kwds):
        pass

    def visit_default(self, type_, field, **kwds):
        if hasattr(type_, 'fields'):
            for t, f in zip(type_, type_.fields):
                return self.visit(t, f, **kwds)
        else:
            try:
                for t in type_:
                    return self.visit(t, **kwds)
            except TypeError:
                pass
