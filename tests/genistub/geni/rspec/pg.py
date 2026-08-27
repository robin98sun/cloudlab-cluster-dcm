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
    def __init__(self, name): self.name = name; self.ifaces = []; self.bandwidth = None
    def addInterface(self, i): self.ifaces.append(i)
class Request:
    def __init__(self): self.nodes = []; self.lans = []
    def RawPC(self, name): n = RawPC(name); self.nodes.append(n); return n
    def LAN(self, name): l = LAN(name); self.lans.append(l); return l
    def dump(self):
        print("  %-6s %-12s %-10s %s" % ("node", "hardware", "lan addr", "blockstore"))
        for n in self.nodes:
            addr = n.ifaces[0].addrs[0].addr if n.ifaces and n.ifaces[0].addrs else "-"
            bs = ", ".join("%s=%s" % (b.mount, b.size) for b in n.blockstores) or "-"
            print("  %-6s %-12s %-10s %s" % (n.name, n.hardware_type or "-", addr, bs))
        print("  LANs: %s" % ", ".join("%s(%d ifaces)" % (l.name, len(l.ifaces))
                                       for l in self.lans))
