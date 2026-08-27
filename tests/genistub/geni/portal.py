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
        # Record the choices so a test can check every hardware parameter
        # offers the same list -- the portal enforces legalValues, and a
        # parameter without them is a free-text box, not a dropdown.
        self._legal = getattr(self, "_legal", {})
        self._legal[name] = ([v for v, _ in legalValues]
                             if legalValues else None)
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
        import json, os
        if os.environ.get("DUMP_PARAM_CHOICES"):
            print("CHOICES " + json.dumps(getattr(self, "_legal", {})))
