"""Distributed admission-control testbed -- CloudLab profile (k3s on bare metal).

Project-neutral: paths, namespace, and labels use the generic name
"testbed", so the same infrastructure serves any system under test.

Physical hosts (one hardware type per comparison series, default c6525-25g;
single shared experiment LAN -- every node needs just one interface):

    ctl1    k3s control plane + monitoring + load generation
    fe<j>   frontend hosts: fe_instances FE+testbed pods each
            (hostNetwork, distinct ports 8081..)
    db<k>   storage hosts: 1 replica pod each (+ declared
            noisy-neighbor pods later)
    lg<i>   optional dedicated load-generator hosts; 0 until
            stub_lg's late_sends gate says otherwise

All roles share ONE experiment LAN ("expt", 10.10.1.0/24). The earlier
client/backend split needed two interfaces on fe hosts and excluded
single-interface types like c6420; the physical admission-bypass isolation
it provided is gone, and the smoke suite reports that honestly (S06).

Every setting is an individual form field: there are no presets and no
dropdowns. A preset bound the same knobs a second time and then silently
overrode what the form said -- that cost real allocations, twice -- and the
hardware dropdowns could not express a type that was not already in the
list, so every real request ended up in the "custom" escape-hatch box next
to them. Sizing guidance that used to live in the presets now lives in the
description of the field it applies to.

After baking a golden image, pin its versioned URN as the disk_image
default here and commit.

Placement rules live in cloudlab/gen_manifests.py: FE and DB never share a
host, DB replicas never share a host, FE pods colocate.

Per-role hardware requirements when substituting hw_type:
    all      : ONE experimental interface suffices (single shared LAN)
    db hosts : local disk large enough for the data blockstore
    fe hosts : cores and network; they carry the admission gates
    lg hosts : cores for pacing; neither disk nor gate work
  fe and lg take separate types because a cluster commonly has plenty of
  one and little of the other, and one shared field failed the whole
  allocation whenever either ran short
    all      : one homogeneous type within any comparison series -- results
               are comparable within a type, never across; S03 warns on
               mixed allocations

Network: one experiment LAN 10.10.1.0/24. Monitoring and all k3s control
traffic ride CloudLab's control network; measured pods use hostNetwork, so
nothing latency-sensitive crosses an overlay.

Address plan: ctl1 10.10.1.10; lg<i> 10.10.1.(10+i); fe<j> 10.10.1.(20+j);
db<k> 10.10.1.(30+k).
"""

import geni.portal as portal
import geni.rspec.pg as pg

BASE_IMAGE = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
# Golden image baked 2026-08-20 (bake layer 1, c6525-25g, Utah). Unversioned
# URN tracks the latest bake; append :N to freeze a version before tagging a
# release. Rebuild from BASE_IMAGE by passing it as disk_image if the golden
# image is ever broken.
GOLDEN_IMAGE = "urn:publicid:IDN+utah.cloudlab.us+image+aces-project-01-PG0:DCM-dev.db1"

# Used when the cluster-wide hardware field is left empty.
DEFAULT_HW = "c6525-25g"

# Which CloudLab cluster each hardware type lives in. A single experiment can
# in principle span aggregates, but a LAN between them is a STITCHED
# wide-area link: tens of milliseconds of round trip against ~0.1 ms on a
# local LAN. Raft would then pay WAN latency on every commit and the
# fsync-bound write capacity this project measures would be replaced by a
# network-bound one -- so mixing types across clusters is refused here rather
# than left to fail at mapping time or, worse, succeed and mislead.
HW_CLUSTER = {
    "c6525-25g": "utah", "c6620": "utah", "d6515": "utah",
    "d7615": "utah", "c6525-100g": "utah",
    "c6420": "clemson", "c6320": "clemson", "r6615": "clemson",
    "r650": "clemson", "r7525": "clemson", "c8220": "clemson",
    "c8220x": "clemson", "c4130": "clemson", "r6525": "clemson",
}

# Types absent from this map are allowed -- CloudLab adds hardware faster
# than this file changes -- they simply cannot participate in the
# cross-cluster check below.

pc = portal.Context()

pc.defineParameter(
    "num_fe_hosts", "Frontend hosts", portal.ParameterType.INTEGER, 10,
    longDescription="Each runs fe_instances FE+testbed pods. One host still "
                    "preserves the multi-upstream property, but front-tier "
                    "throughput must scale with the load tier or it becomes "
                    "the hidden ceiling -- 10 per 3 storage hosts is the ratio "
                    "that held. ANY count is accepted, including 0, which "
                    "places no frontend at all: the admission path then has "
                    "nothing to run on. Useful for holding an allocation or "
                    "exercising storage alone, not for a measurement.")
pc.defineParameter(
    "num_db_hosts", "Storage hosts", portal.ParameterType.INTEGER, 1,
    longDescription="One replica pod per host. ANY count from 1 up is "
                    "accepted, including even ones, so a storage tier can be "
                    "assembled from isolated free machines one at a time. "
                    "The Raft arithmetic, so the cost of each choice is "
                    "visible rather than enforced: N hosts need a majority "
                    "of N//2+1 and therefore tolerate (N-1)//2 failures. 1 "
                    "tolerates none and is a complete, useful single-node "
                    "testbed. 2 also tolerates none -- the majority is 2, so "
                    "either host failing stops writes -- which buys nothing "
                    "over 1 except replication surface, and is worth running "
                    "only while on the way to 3. 3 tolerates one. 4 also "
                    "tolerates one, so it costs a machine for nothing. 5 "
                    "tolerates two. In short the odd counts are the "
                    "efficient ones and the even counts are transitional; "
                    "pick an even count deliberately, not by accident. 0 is "
                    "accepted too and places no storage, which leaves "
                    "nothing to admit against.")
pc.defineParameter(
    "num_lg_hosts", "Dedicated load-generator hosts",
    portal.ParameterType.INTEGER, 10,
    longDescription="Open-loop pacing holds about one agent per physical "
                    "core at ~2.4k qps each (~43k/host on 20-core machines), "
                    "and a fleet that cannot outrun the storage tier measures "
                    "ITSELF -- four hosts topped out at 55% of pod CPU and "
                    "the pace gate voided everything above it. 10 per 3 NVMe "
                    "storage hosts. 0 runs load generation on ctl1 (smoke "
                    "only).")
pc.defineParameter(
    "fe_instances", "FE pods per frontend host",
    portal.ParameterType.INTEGER, 3,
    longDescription="Ports 8081, 8082, ... Three is the minimum for the "
                    "distributed property: several independent admission "
                    "points enforcing one shared budget -- so 1 or 2 still "
                    "runs but measures something else, and 0 places no pods "
                    "on frontend hosts that still get allocated. No count is "
                    "refused.")
pc.defineParameter(
    "hw_type", "Hardware type", portal.ParameterType.STRING, DEFAULT_HW,
    longDescription="Free text -- any CloudLab type name, whether or not it "
                    "predates this file. The topology needs only ONE "
                    "experimental interface per node (single shared LAN), so "
                    "any type maps. Used for every role that does not name "
                    "its own type below. Availability is per-type and shifts "
                    "hourly: check the cluster status page for enough free "
                    "machines before instantiating. One homogeneous type per "
                    "comparison series. Clemson: c6420, c6320, c8220, "
                    "c8220x, c4130, r650, r6615, r6525, r7525. Utah: "
                    "c6525-25g, c6525-100g, c6620, d6515, d7615. MIXING TWO "
                    "CLUSTERS IS ACCEPTED AND IS ALMOST ALWAYS WRONG: a LAN "
                    "between aggregates is a stitched wide-area link, tens "
                    "of ms RTT against ~0.1 ms locally, which silently "
                    "changes every latency and every watermark measured "
                    "here. Nothing blocks it; the numbers just stop meaning "
                    "what they normally mean.")
pc.defineParameter(
    "storage_hw_type", "Storage-host hardware type(s) (empty = same as above)",
    portal.ParameterType.STRING, "",
    longDescription="Hardware for the db hosts. These want the local disk, "
                    "so this is the one worth naming separately. Accepts a "
                    "COMMA-SEPARATED LIST, one entry per db host, so a "
                    "3-node storage cluster can be assembled out of the "
                    "singletons a cluster happens to have free -- e.g. "
                    "'r6525,r6525,r6515'. A single name applies to every db "
                    "host, which is what this field meant before it took a "
                    "list. A list SHORTER than the host count cycles -- two "
                    "types across four hosts gives two of each -- and a "
                    "longer one is truncated; neither is refused. NVMe is "
                    "preferred and "
                    "SATA SSD is the fallback; spinning disk is not usable "
                    "here. Every entry must still be from the SAME cluster "
                    "as the other roles.")
pc.defineParameter(
    "load_hw_type", "Load-driver hardware type, lg hosts (empty = same as above)",
    portal.ParameterType.STRING, "",
    longDescription="Pacing needs cores, not fast storage.")
pc.defineParameter(
    "fe_hw_type", "Frontend hardware type, fe hosts (empty = same as the "
                  "load-driver type)",
    portal.ParameterType.STRING, "",
    longDescription="Frontend hosts carry the admission gates and every pod's "
                    "share of the offered load, so they want cores and network "
                    "rather than disk. Separate from the load-driver type "
                    "because a cluster often has plenty of one and little of "
                    "the other, and pinning both to one type fails the whole "
                    "allocation when either runs short. Empty follows the "
                    "load-driver type, which is what this did before it was "
                    "split.")
pc.defineParameter(
    "ctl_hw_type", "Control/observer hardware type (empty = same as above)",
    portal.ParameterType.STRING, "",
    longDescription="Hardware for ctl1.")
pc.defineParameter(
    "disk_image", "Disk image URN", portal.ParameterType.STRING, GOLDEN_IMAGE,
    longDescription="Defaults to the golden image (~15-minute redeploy). Use "
                    "BASE_IMAGE from the profile source to rebuild from "
                    "scratch.")
pc.defineParameter(
    "data_size", "Data blockstore per storage host",
    portal.ParameterType.STRING, "600GB",
    longDescription="Mounted at /mnt/data. This does NOT gate access to the "
                    "hardware -- the node is yours and every physical disk is "
                    "present and writable as a raw device either way. What it "
                    "gates is what CloudLab automatically carves, formats and "
                    "mounts for you; unrequested space simply stays "
                    "unpartitioned. Size it past RAM so a working set can "
                    "exceed the page cache, which the cache-failure scenario "
                    "needs. Checked at MAPPING time against free space on the "
                    "node's spare disks: if instantiation fails complaining "
                    "about space, lower it. Empty skips the blockstore (data "
                    "lands on /local -- smoke only).")
# Ten CUSTOM HOST SLOTS. Each is one machine, requested only when its
# hardware type is filled in, so a running experiment can absorb whatever
# a cluster happens to have free -- one isolated idle host at a time --
# without disturbing the nodes it already holds. Type a name into cm1,
# Modify; later type one into cm2, Modify again. The portal adds the new
# node and leaves the existing mapping alone.
#
# They are deliberately UNASSIGNED to a tier. A machine grabbed because it
# was free is not yet known to be a load host or a storage host; it joins
# k3s labelled testbed/role=cm-host and the experiment decides afterwards.
# That is why these are ten separate fields rather than a count plus one
# type: the whole point is that the slots are filled with DIFFERENT types,
# at different times, as availability appears.
for _m in range(1, 11):
    pc.defineParameter(
        "cm%d_hw_type" % _m,
        "Custom host cm%d -- hardware type (empty = not requested)" % _m,
        portal.ParameterType.STRING, "",
        longDescription="One extra machine of this CloudLab type, named "
                        "cm%d, address 10.10.1.%d. Empty leaves the slot "
                        "unused. Must be from the same cluster as every "
                        "other role: a LAN across aggregates is a stitched "
                        "wide-area link. Unassigned to any tier -- it joins "
                        "the cluster and waits to be given a job."
                        % (_m, 40 + _m),
        advanced=True)
pc.defineParameter(
    "cm_data_size", "Data blockstore per custom host (empty = none)",
    portal.ParameterType.STRING, "",
    longDescription="Applies to every cm host that is requested. Leave "
                    "empty unless a custom host is meant to carry storage; "
                    "an absorbed idle machine is more often wanted for "
                    "cores. Same semantics as the storage-host blockstore: "
                    "it gates what CloudLab carves and mounts at /mnt/data, "
                    "not access to the disks, and it is checked at MAPPING "
                    "time against the node's free space.",
    advanced=True)
pc.defineParameter(
    "client_bw", "Client link bandwidth (Kbps, 0 = native)",
    portal.ParameterType.INTEGER, 0)
pc.defineParameter(
    "backend_bw", "Backend link bandwidth (Kbps, 0 = native)",
    portal.ParameterType.INTEGER, 0)

params = pc.bindParameters()

CONFIG_FIELDS = ("num_fe_hosts", "num_db_hosts", "num_lg_hosts",
                 "fe_instances", "hw_type", "storage_hw_type", "load_hw_type",
                 "fe_hw_type", "ctl_hw_type", "disk_image", "data_size", "client_bw",
                 "backend_bw", "cm_data_size") + tuple(
                     "cm%d_hw_type" % _m for _m in range(1, 11))
cfg = {f: getattr(params, f) for f in CONFIG_FIELDS}
for _f in ("hw_type", "storage_hw_type", "load_hw_type", "fe_hw_type",
           "ctl_hw_type",
           "disk_image", "data_size", "cm_data_size") + tuple(
               "cm%d_hw_type" % _m for _m in range(1, 11)):
    cfg[_f] = cfg[_f].strip()

# The slots that were actually filled in, in order. A gap is not an error:
# leaving cm2 empty and filling cm3 is exactly what happens when a type
# stops being available between one Modify and the next.
cfg["cm_hosts"] = [(_m, cfg["cm%d_hw_type" % _m]) for _m in range(1, 11)
                   if cfg["cm%d_hw_type" % _m]]
if not cfg["hw_type"]:
    cfg["hw_type"] = DEFAULT_HW
# Every per-role type falls back to the cluster-wide one, so naming none of
# them gives the homogeneous request unchanged.
# fe_hw_type falls back to the LOAD type, not the cluster-wide one: before
# the split these two shared a field, so an unset fe type must still land
# wherever load_hw_type points or an existing invocation changes meaning.
# ...but only when the load type is a single name. Once load carries a
# per-host list that list is sized to num_lg_hosts, and copying it into a
# frontend tier of a different size is nonsense; fall through to hw_type.
if not cfg["fe_hw_type"] and "," not in cfg["load_hw_type"]:
    cfg["fe_hw_type"] = cfg["load_hw_type"]
for _g in ("storage_hw_type", "load_hw_type", "fe_hw_type", "ctl_hw_type"):
    if not cfg[_g]:
        cfg[_g] = cfg["hw_type"]

# Storage, load and frontend hardware may each be heterogeneous: a
# comma-separated list, one entry per host in that role. A tier is often
# only reachable by taking the two of one type and the one of another that
# a cluster has free, and refusing that mix would mean no tier at all.
# It also lets a RUNNING experiment grow onto whatever is free now without
# disturbing the hosts it already has: keep the existing entries and append
# the new type, and the portal's Modify adds hosts rather than remapping
# the ones already provisioned. One entry applies to every host in the
# role, so a plain type name keeps its old meaning.


def _expand_hw(field, count_field, role_label):
    """One hardware type per host in a role. Never refuses a list length.

    A short list CYCLES and a long one is truncated, so any count of types
    maps onto any count of hosts. This used to be an error, which meant that
    changing a host count without also editing the type list was a form
    rejection rather than a sensible request. Cycling also makes the common
    incremental case work by itself: 'r6615,r6525' across four hosts gives
    two of each.

    It must not raise either. The refusal was the only thing standing between
    a short list and an IndexError at node-build time, so removing the one
    without fixing the other would turn a clear message into a traceback.
    """
    types = [_t.strip() for _t in cfg[field].split(",") if _t.strip()]
    count = max(cfg[count_field], 0)
    if not types:
        return []
    return [types[_i % len(types)] for _i in range(count)]


cfg["storage_hw_types"] = _expand_hw("storage_hw_type", "num_db_hosts",
                                     "storage")
cfg["load_hw_types"] = _expand_hw("load_hw_type", "num_lg_hosts", "load")
cfg["fe_hw_types"] = _expand_hw("fe_hw_type", "num_fe_hosts", "frontend")

# Every type that PLACES A NODE must live in the same cluster. The check
# runs over the three role types only: once every role has an explicit
# type, the cluster-wide hw_type places nothing -- it is a fallback
# source, already propagated above. Including it compared the default
# (utah c6525-25g) against a fully-Clemson selection and refused a
# request that named no utah node at all.
_pairs = [("ctl_hw_type", cfg["ctl_hw_type"])]
# Each entry is checked on its own: a heterogeneous tier is fine, a tier
# straddling two aggregates is not.
for _field, _key in (("storage_hw_type", "storage_hw_types"),
                     ("load_hw_type", "load_hw_types"),
                     ("fe_hw_type", "fe_hw_types")):
    _pairs += [("%s[%d]" % (_field, _i), _t)
               for _i, _t in enumerate(cfg[_key])]
# A custom slot is the MOST likely place to stitch an aggregate by
# accident: its whole purpose is to grab whatever is free, and what is
# free is often free because it is in the other cluster.
_pairs += [("cm%d_hw_type" % _m, _t) for _m, _t in cfg["cm_hosts"]]
_seen = {}
for _f, _t in _pairs:
    _cl = HW_CLUSTER.get(_t)
    if _cl:
        _seen.setdefault(_cl, []).append("%s=%s" % (_f, _t))
# Keep the unused fallback consistent with the chosen cluster, so any
# future role that falls back to hw_type cannot stitch a wide-area LAN.
# A request spanning two aggregates is NO LONGER REFUSED. It is still a bad
# idea -- the LAN becomes a stitched wide-area link, tens of ms RTT against
# ~0.1 ms locally, which silently changes every latency this testbed measures
# -- and that warning now lives in the hardware fields' descriptions instead
# of in a block. Deciding it is the operator's job.
if len(_seen) == 1:
    _used_cl = next(iter(_seen))
    if HW_CLUSTER.get(cfg["hw_type"]) not in (None, _used_cl):
        # Any type from the chosen cluster will do; storage is not guaranteed
        # to have one now that a role may legally have zero hosts.
        _fallbacks = (cfg["storage_hw_types"] + cfg["fe_hw_types"]
                      + cfg["load_hw_types"]
                      + [_t for _, _t in cfg["cm_hosts"]])
        _fallbacks = [_t for _t in _fallbacks
                      if HW_CLUSTER.get(_t) == _used_cl]
        if _fallbacks:
            cfg["hw_type"] = _fallbacks[0]

# NO COUNT IS REFUSED, including zero. Every role loop is range(1, n + 1), so
# a zero or negative count simply places no nodes of that role, and ctl1 is
# always present -- the request is never empty. A partial testbed is a normal
# thing to want: storage hosts alone to seed a volume, frontends alone to
# check pod placement, or a bare ctl1 to hold an allocation while machines
# free up. What each count costs is described on the field itself.
# Even storage counts are NO LONGER REFUSED. The old rule rejected exactly 2
# on the grounds that it tolerates no failures, which is true -- and it also
# made it impossible to GROW a tier one host at a time, which is how a tier
# gets built out of whatever a cluster happens to have free. Going 1 -> 3 in
# one step needs two matching machines to appear at once; going 1 -> 2 -> 3
# needs one at a time, and the profile already supports exactly that for
# hardware types (see _expand_hw, which exists so a running experiment can
# absorb a free host without remapping the ones it has). Refusing the count
# while supporting the growth was the two halves disagreeing.
#
# The arithmetic is now in the field's longDescription instead, where the
# portal shows it: an even count buys no fault tolerance over the odd count
# below it. That is a cost to accept knowingly, not a configuration to block.
pc.verifyParameters()

request = pc.makeRequestRSpec()

# ONE experiment LAN. The earlier client/backend split required two
# interfaces on fe hosts, which excluded every single-interface hardware
# type (c6420 failed mapping with "2 requested, 1 found"). All roles now
# share one L2 segment; the admission-bypass isolation that the split
# enforced physically is no longer provided by topology, and the smoke
# checks say so instead of failing.
expt_lan = request.LAN("expt")
if cfg["client_bw"] > 0:
    expt_lan.bandwidth = cfg["client_bw"]
# backend_bw is retained as an accepted parameter for old bookmarked URLs but
# is a no-op: there is no second LAN any more.


def make_node(name, role, extra_args="", hw=None):
    node = request.RawPC(name)
    # Per-group hardware type. db hosts pass their own, since the storage
    # tier may be heterogeneous.
    if hw is None:
        hw = cfg["ctl_hw_type"] if role == "ctl" else cfg["hw_type"]
    if hw:
        node.hardware_type = hw
    node.disk_image = cfg["disk_image"]
    node.addService(pg.Execute(
        shell="bash",
        command="bash /local/repository/cloudlab/bootstrap.sh %s%s"
                % (role, extra_args)))
    return node


def attach(node, lan, addr):
    iface = node.addInterface()
    iface.addAddress(pg.IPv4Address(addr, "255.255.255.0"))
    lan.addInterface(iface)


ctl = make_node("ctl1", "ctl",
                " --fe-hosts %d --db-hosts %d --lg-hosts %d --fe-instances %d"
                " --cm-hosts %d"
                % (cfg["num_fe_hosts"], cfg["num_db_hosts"],
                   cfg["num_lg_hosts"], cfg["fe_instances"],
                   len(cfg["cm_hosts"])))
attach(ctl, expt_lan, "10.10.1.10")

for i in range(1, cfg["num_lg_hosts"] + 1):
    n = make_node("lg%d" % i, "lg", hw=cfg["load_hw_types"][i - 1])
    attach(n, expt_lan, "10.10.1.%d" % (10 + i))

for j in range(1, cfg["num_fe_hosts"] + 1):
    n = make_node("fe%d" % j, "fe", hw=cfg["fe_hw_types"][j - 1])
    attach(n, expt_lan, "10.10.1.%d" % (20 + j))

for k in range(1, cfg["num_db_hosts"] + 1):
    n = make_node("db%d" % k, "db", hw=cfg["storage_hw_types"][k - 1])
    attach(n, expt_lan, "10.10.1.%d" % (30 + k))
    if cfg["data_size"]:
        bs = n.Blockstore("db%d-data" % k, "/mnt/data")
        bs.size = cfg["data_size"]

# Custom hosts last, so their addresses never move when a tier grows: the
# slot number fixes the address (cm3 is always 10.10.1.43), not the order
# in which the slots were filled. An absorbed host that changed address
# because another one was added later would invalidate every config that
# already named it.
for m, cm_hw in cfg["cm_hosts"]:
    n = make_node("cm%d" % m, "cm", hw=cm_hw)
    attach(n, expt_lan, "10.10.1.%d" % (40 + m))
    if cfg["cm_data_size"]:
        bs = n.Blockstore("cm%d-data" % m, "/mnt/data")
        bs.size = cfg["cm_data_size"]

pc.printRequestRSpec(request)
