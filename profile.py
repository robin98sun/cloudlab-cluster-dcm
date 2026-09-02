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
                    "the hidden ceiling. 10 per 3 storage hosts.")
pc.defineParameter(
    "num_db_hosts", "Storage hosts", portal.ParameterType.INTEGER, 1,
    longDescription="One replica pod per host. 1 is a complete, useful "
                    "single-node testbed; 3 gives a Raft majority that "
                    "tolerates one failure. 2 is refused: it survives no "
                    "failures at all, like 1, but with twice the surface.")
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
                    "points enforcing one shared budget.")
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
                    "c6525-25g, c6525-100g, c6620, d6515, d7615.")
pc.defineParameter(
    "storage_hw_type", "Storage-host hardware type (empty = same as above)",
    portal.ParameterType.STRING, "",
    longDescription="Hardware for the db hosts. These want the local disk, "
                    "so this is the one worth naming separately.")
pc.defineParameter(
    "load_hw_type", "Load-driver hardware type, lg + fe hosts (empty = same as above)",
    portal.ParameterType.STRING, "",
    longDescription="Pacing needs cores, not fast storage.")
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
pc.defineParameter(
    "client_bw", "Client link bandwidth (Kbps, 0 = native)",
    portal.ParameterType.INTEGER, 0)
pc.defineParameter(
    "backend_bw", "Backend link bandwidth (Kbps, 0 = native)",
    portal.ParameterType.INTEGER, 0)

params = pc.bindParameters()

CONFIG_FIELDS = ("num_fe_hosts", "num_db_hosts", "num_lg_hosts",
                 "fe_instances", "hw_type", "storage_hw_type", "load_hw_type",
                 "ctl_hw_type", "disk_image", "data_size", "client_bw",
                 "backend_bw")
cfg = {f: getattr(params, f) for f in CONFIG_FIELDS}
for _f in ("hw_type", "storage_hw_type", "load_hw_type", "ctl_hw_type",
           "disk_image", "data_size"):
    cfg[_f] = cfg[_f].strip()
if not cfg["hw_type"]:
    cfg["hw_type"] = DEFAULT_HW
# Every per-role type falls back to the cluster-wide one, so naming none of
# them gives the homogeneous request unchanged.
for _g in ("storage_hw_type", "load_hw_type", "ctl_hw_type"):
    if not cfg[_g]:
        cfg[_g] = cfg["hw_type"]

# Every type that PLACES A NODE must live in the same cluster. The check
# runs over the three role types only: once every role has an explicit
# type, the cluster-wide hw_type places nothing -- it is a fallback
# source, already propagated above. Including it compared the default
# (utah c6525-25g) against a fully-Clemson selection and refused a
# request that named no utah node at all.
_seen = {}
for _f in ("storage_hw_type", "load_hw_type", "ctl_hw_type"):
    _cl = HW_CLUSTER.get(cfg[_f])
    if _cl:
        _seen.setdefault(_cl, []).append("%s=%s" % (_f, cfg[_f]))
# Keep the unused fallback consistent with the chosen cluster, so any
# future role that falls back to hw_type cannot stitch a wide-area LAN.
if len(_seen) == 1:
    _used_cl = next(iter(_seen))
    if HW_CLUSTER.get(cfg["hw_type"]) not in (None, _used_cl):
        cfg["hw_type"] = cfg["storage_hw_type"]
if len(_seen) > 1:
    pc.reportError(portal.ParameterError(
        "hardware types span %d CloudLab clusters (%s). A LAN between "
        "aggregates is a stitched wide-area link, tens of ms RTT against "
        "~0.1ms locally. Pick every type from one cluster."
        % (len(_seen), "; ".join("%s: %s" % (c, ", ".join(v))
                                 for c, v in sorted(_seen.items()))),
        ["hw_type", "storage_hw_type", "load_hw_type", "ctl_hw_type"]))

if cfg["num_fe_hosts"] < 1:
    pc.reportError(portal.ParameterError(
        "At least one frontend host is required.", ["num_fe_hosts"]))
if cfg["num_db_hosts"] < 1:
    pc.reportError(portal.ParameterError(
        "At least one storage host is required.", ["num_db_hosts"]))
if cfg["num_db_hosts"] == 2:
    pc.reportError(portal.ParameterError(
        "Two storage hosts cannot form a useful Raft majority. Use 1 (smoke) "
        "or 3.", ["num_db_hosts"]))
if cfg["fe_instances"] < 1:
    pc.reportError(portal.ParameterError(
        "At least one FE pod per host.", ["fe_instances"]))
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


def make_node(name, role, extra_args=""):
    node = request.RawPC(name)
    # Per-group hardware type.
    hw = {"db": cfg["storage_hw_type"],
          "lg": cfg["load_hw_type"], "fe": cfg["load_hw_type"],
          "ctl": cfg["ctl_hw_type"]}.get(role, cfg["hw_type"])
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
                % (cfg["num_fe_hosts"], cfg["num_db_hosts"],
                   cfg["num_lg_hosts"], cfg["fe_instances"]))
attach(ctl, expt_lan, "10.10.1.10")

for i in range(1, cfg["num_lg_hosts"] + 1):
    n = make_node("lg%d" % i, "lg")
    attach(n, expt_lan, "10.10.1.%d" % (10 + i))

for j in range(1, cfg["num_fe_hosts"] + 1):
    n = make_node("fe%d" % j, "fe")
    attach(n, expt_lan, "10.10.1.%d" % (20 + j))

for k in range(1, cfg["num_db_hosts"] + 1):
    n = make_node("db%d" % k, "db")
    attach(n, expt_lan, "10.10.1.%d" % (30 + k))
    if cfg["data_size"]:
        bs = n.Blockstore("db%d-data" % k, "/mnt/data")
        bs.size = cfg["data_size"]

pc.printRequestRSpec(request)
