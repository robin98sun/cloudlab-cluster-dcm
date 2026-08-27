"""Minimal stand-in for geni.portal, enough to execute a CloudLab profile
locally and record exactly what it requests. geni-lib itself does not install
on a modern Python, so this is the only way to see the rspec this profile
produces without the portal."""
class ParameterType:
    STRING = "string"; INTEGER = "integer"; BOOLEAN = "boolean"
class ParameterError(Exception):
    def __init__(self, msg, fields=None): super().__init__(msg); self.fields = fields
class _Params: pass
class Context:
    def __init__(self): self._defs = {}; self._errors = []; self.bound = _Params()
    def defineParameter(self, name, desc, ptype, default, legalValues=None,
                        longDescription=None):
        self._defs[name] = default
    def bindParameters(self):
        import os, json
        over = json.loads(os.environ.get("PROFILE_PARAMS", "{}"))
        for k, v in self._defs.items(): setattr(self.bound, k, over.get(k, v))
        for k, v in over.items(): setattr(self.bound, k, v)
        return self.bound
    def reportError(self, e): self._errors.append(str(e))
    def verifyParameters(self):
        if self._errors: raise SystemExit("parameter errors: %s" % self._errors)
    def makeRequestRSpec(self):
        from geni.rspec.pg import Request
        self.req = Request(); return self.req
    def printRequestRSpec(self, req=None):
        (req or self.req).dump()
