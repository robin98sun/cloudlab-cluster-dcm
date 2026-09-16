class IPv4Address:
    def __init__(self, addr, mask): self.addr = addr; self.mask = mask
class _Iface:
    def __init__(self): self.addrs = []
    def addAddress(self, a): self.addrs.append(a)
class Execute:
    def __init__(self, shell=None, command=None): self.command = command
class _Blockstore:
    def __init__(self, name, mount): self.name = name; self.mount = mount; self.size = None
class RawPC:
    def __init__(self, name):
        self.name = name; self.hardware_type = None; self.disk_image = None
        self.ifaces = []; self.services = []; self.blockstores = []
    def addInterface(self): i = _Iface(); self.ifaces.append(i); return i
    def addService(self, s): self.services.append(s)
    def Blockstore(self, name, mount):
        b = _Blockstore(name, mount); self.blockstores.append(b); return b
class LAN:
    # bandwidth was stored and never printed, so client_bw was set-but-
    # unobservable -- the same shape as disk_image, which cost an allocation.
    def __init__(self, name): self.name = name; self.ifaces = []; self.bandwidth = None
    def addInterface(self, i): self.ifaces.append(i)
class Request:
    def __init__(self): self.nodes = []; self.lans = []
    def RawPC(self, name): n = RawPC(name); self.nodes.append(n); return n
    def LAN(self, name): l = LAN(name); self.lans.append(l); return l
    def dump(self):
        print("  %-6s %-12s %-10s %-22s %s"
              % ("node", "hardware", "lan addr", "blockstore", "image"))
        for n in self.nodes:
            addr = n.ifaces[0].addrs[0].addr if n.ifaces and n.ifaces[0].addrs else "-"
            bs = ", ".join("%s=%s" % (b.mount, b.size) for b in n.blockstores) or "-"
            # The image is per NODE and a wrong one is a MAPPER refusal of the
            # whole topology, before any node boots. It was invisible here.
            print("  %-6s %-12s %-10s %-22s %s"
                  % (n.name, n.hardware_type or "-", addr, bs,
                     n.disk_image or "-"))
        # The bootstrap ARGUMENTS decide which node runs the control plane and
        # how agents find it. They were invisible here, so the half of the
        # profile that matters most at bring-up could not be tested at all.
        for n in self.nodes:
            for sv in n.services:
                print("  CMD %s %s" % (n.name, sv.command))
        print("  LANs: %s" % ", ".join("%s(%d ifaces,bw=%s)"
                                       % (l.name, len(l.ifaces),
                                          l.bandwidth if l.bandwidth else "-")
                                       for l in self.lans))
