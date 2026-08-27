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

Purposes are parameter bindings of this one generator, selected by `preset`:

    preset       machines  db hosts  intent
    smoke        3         1         plumbing verification, fast iteration
    full         5         3         replicated/Raft baseline experiments
    submission   5         3         frozen bindings for reported results;
                                     bind a portal profile to a release TAG
                                     of this repo so it can never drift
    custom       --        --        the individual form fields apply

When preset != custom, the preset's bindings OVERRIDE the individual form
fields they name; fields a preset does not name (notably disk_image) still
come from the form. Hardware type is the ONE exception: an explicit pick in
hw_type, or anything in hw_type_custom, outranks the preset, because
availability changes hour to hour and a preset must never pin a user to a
type that cannot map. hw_type defaults to "" (unset) so that "chose
c6525-25g" is distinguishable from "left it alone". After baking a golden image, pin its versioned URN as
the disk_image default here and commit; before tagging a submission release,
also pin it inside the submission preset.

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
# URN tracks the latest bake; append :N to freeze a version (do that inside
# the submission preset before tagging a release). Rebuild from BASE_IMAGE
# by passing it as disk_image if the golden image is ever broken.
GOLDEN_IMAGE = "urn:publicid:IDN+utah.cloudlab.us+image+aces-project-01-PG0:DCM-dev.db1"

# Used when neither the form nor the preset names a type. The dropdown's
# default is "" (unset) rather than this value, so that an explicit pick can
# be told apart from an untouched field -- that distinction is what lets a
# selection override a preset.
DEFAULT_HW = "c6525-25g"

# STORAGE hardware is the scarce resource; everything else is not.
#
# The measured reason: write capacity is fsync-bound, and the log device sets
# it -- 19.5 qps on a 7200RPM disk against thousands on flash, on identical
# code and MORE cores (doc 11). Reads and the load generators are bound by
# CPU and network, which the plentiful machines have in abundance. So a
# cluster that puts flash under the replicas and spends commodity nodes on
# the control plane, the front tier and the load drivers gets the property
# that matters while asking the scheduler for only three scarce machines.
#
# It also maps far more often. A request for eight of a type with four free
# fails outright; a request for three of that type plus five of a plentiful
# one succeeds -- which is the difference between running tonight and
# queueing for a reservation.
STORAGE_HW_DEFAULT = ""      # "" means: use the cluster-wide hw_type

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
    "r650": "clemson", "r7525": "clemson",
}

# Per-core clock, for the roles that are single-thread bound. The load
# driver's pacer is ONE goroutine per agent firing the whole schedule for its
# share, so how much load a machine can offer scales with clock and
# generation rather than with core count -- measured at ~2500 qps per pacer
# on 2.6GHz Skylake. A 28-core 2014 Haswell at 2.0GHz is the worst choice
# for that role despite looking respectable on a core count.
# Disk class per type -- the property that sets write capacity. Measured:
# 19.5 write qps on 7200RPM spindles against ~3,500 on SATA SSD, same code,
# more cores (doc 11). "hdd" here is not a detail, it is the ceiling.
HW_DISK = {
    "c6525-25g": "ssd", "c6620": "nvme", "d6515": "ssd", "d7615": "nvme",
    "c6525-100g": "nvme",
    "c6420": "hdd", "c6320": "hdd", "r7525": "hdd", "r650": "nvme",
    "r6615": "nvme", "c8220": "hdd", "c8220x": "hdd", "c4130": "hdd",
    "ibm8335": "hdd",
}

HW_GHZ = {
    "c6525-25g": 3.00, "c6620": 2.1, "d6515": 2.35, "d7615": 3.25,
    "c6525-100g": 2.80, "c6420": 2.6, "c6320": 2.0, "r7525": 2.9,
    "r650": 2.4, "r6615": 3.25, "c8220": 2.2, "c8220x": 2.2,
    "c4130": 2.5, "ibm8335": 2.86,
}

PRESETS = {
    "smoke": dict(num_fe_hosts=1, num_db_hosts=1, num_lg_hosts=0,
                  fe_instances=3, data_size="20GB"),
    "full": dict(num_fe_hosts=1, num_db_hosts=3, num_lg_hosts=0,
                 fe_instances=3, data_size="600GB"),
    # measurement-v1: the measurement-valid topology (8 machines).
    # 3 independent FE hosts (1 controller instance each = independent
    # failure domains and resources), dedicated load generator, monitor/
    # orchestrator free of load generation. Runs are measurement-valid only
    # when the validity gates in the runbook also pass.
    "measurement": dict(num_fe_hosts=3, num_db_hosts=3, num_lg_hosts=1,
                        fe_instances=1, hw_type="c6525-25g",
                        data_size="600GB", client_bw=0, backend_bw=0),
    # Heterogeneous: flash under the replicas, commodity everywhere else.
    # Only THREE machines of the scarce type are requested, which is what
    # makes this mappable when the fast pool is nearly full.
    # Clemson today: c6320 is usually the ONLY type with free nodes, and
    # every free Clemson type is HDD-only. This preset is what actually
    # maps there -- good for read-side and overload work, spindle-bound for
    # writes, and honest about which.
    "clemson-available": dict(num_fe_hosts=3, num_db_hosts=3, num_lg_hosts=1,
                              fe_instances=1, hw_type="c6320",
                              storage_hw_type="c6320", load_hw_type="c6320",
                              ctl_hw_type="c6320", data_size="600GB",
                              client_bw=0, backend_bw=0),
    # Storage on the NVMe machine with the fastest cores; load generation on
    # the machine with the most fast cores, since the pacer is single-thread
    # bound; the observer on whatever is plentiful. ASPIRATIONAL at Clemson:
    # r6615 and r7525 are routinely at zero free.
    "measurement-het": dict(num_fe_hosts=3, num_db_hosts=3, num_lg_hosts=1,
                            fe_instances=1, hw_type="c6420",
                            storage_hw_type="r6615", load_hw_type="r7525",
                            ctl_hw_type="c6420", data_size="600GB",
                            client_bw=0, backend_bw=0),
    # Freeze everything that defines the reported configuration. Pin
    # disk_image here too once the submission-era golden image exists.
    "submission": dict(num_fe_hosts=3, num_db_hosts=3, num_lg_hosts=1,
                       fe_instances=1, hw_type="c6525-25g", data_size="600GB",
                       client_bw=0, backend_bw=0),
}

pc = portal.Context()

pc.defineParameter(
    "preset", "Configuration preset", portal.ParameterType.STRING, "smoke",
    legalValues=[("smoke", "smoke: 3 machines, 1 db host (plumbing-valid)"),
                 ("full", "full: 5 machines, 3 db hosts (plumbing-valid)"),
                 ("measurement", "measurement: 8 machines, 3 FE hosts, dedicated LG"),
                 ("measurement-het", "measurement-het: 3 NVMe storage + fast "
                  "load drivers (Clemson: often unmappable, check free counts)"),
                 ("clemson-available", "clemson-available: all c6320 -- what "
                  "actually maps at Clemson today; HDD, so writes are "
                  "spindle-bound"),
                 ("submission", "submission: frozen measurement-scale bindings"),
                 ("custom", "custom: use the individual fields below")],
    longDescription="Anything other than 'custom' overrides the individual "
                    "fields it defines (see the profile source for exact "
                    "bindings). Presets are versioned with the repository, so "
                    "every result bundle can name its configuration by commit.")
pc.defineParameter(
    "num_fe_hosts", "Frontend hosts (custom preset)",
    portal.ParameterType.INTEGER, 1,
    longDescription="Each runs fe_instances FE+testbed pods; one host already "
                    "preserves the multi-upstream property.")
pc.defineParameter(
    "num_db_hosts", "Storage hosts (custom preset)",
    portal.ParameterType.INTEGER, 1,
    longDescription="One replica pod per host. 1 for smoke, 3 for Raft. "
                    "2 is refused.")
pc.defineParameter(
    "num_lg_hosts", "Dedicated load-generator hosts (custom preset)",
    portal.ParameterType.INTEGER, 0,
    longDescription="0 runs load generation on ctl1. Add hosts only when "
                    "stub_lg's late_sends gate shows pacing degradation.")
pc.defineParameter(
    "fe_instances", "FE pods per frontend host (custom preset)",
    portal.ParameterType.INTEGER, 3,
    longDescription="Ports 8081, 8082, ... Three is the minimum for the "
                    "distributed property: several independent "
                    "admission points enforcing one shared budget.")
pc.defineParameter(
    "hw_type", "Hardware type", portal.ParameterType.STRING, "",
    legalValues=[
        ("", "preset default (c6525-25g) -- leave to let the preset decide"),
        # Labels carry CPU, CLOCK and era, because core count alone is
        # misleading: c6320 has 28 cores of 2014 Haswell at 2.0GHz, while
        # r6615 has 32 cores of 2025 Genoa at 3.25GHz. Anything bound by
        # single-thread speed -- the driver's pacer goroutine, a raft
        # follower's append path -- cares about the clock and the generation,
        # not the total.
        ("c6525-25g", "Utah c6525-25g: AMD 7302P 16c @3.00GHz '22, 128GB, SATA SSD"),
        ("c6620", "Utah c6620: Xeon Gold 5512U 28c @2.1GHz '25, 128GB, NVMe"),
        ("d6515", "Utah d6515: AMD 7452 32c @2.35GHz, 128GB, SATA SSD"),
        ("d7615", "Utah d7615: AMD 9354P 32c @3.25GHz '25, 192GB, NVMe -- only 6"),
        ("c6525-100g", "Utah c6525-100g: AMD 7402P 24c @2.80GHz '22, 128GB, NVMe"),
        ("c6420", "Clemson c6420: 2x Xeon Gold 6142 32c @2.6GHz '17, 384GB, HDD -- plentiful"),
        ("c6320", "Clemson c6320: 2x E5-2683v3 28c @2.0GHz '14, 256GB, HDD -- oldest cores, but usually the ONLY Clemson type free"),
        ("c8220", "Clemson c8220: 2x E5-2660v2 20c @2.2GHz '13, 256GB, HDD"),
        ("r7525", "Clemson r7525: 2x AMD 7542 64c @2.9GHz '20, 512GB, HDD -- best load driver"),
        ("r650", "Clemson r650: 2x Xeon Plat 8360Y 72c @2.4GHz, 256GB, SSD+NVMe"),
        ("r6615", "Clemson r6615: AMD 9354P 32c @3.25GHz '25, 192GB, 2x NVMe -- BEST storage"),
    ],
    longDescription="The topology needs only ONE experimental interface per "
                    "node (single shared LAN), so any listed type maps. "
                    "An explicit pick here OVERRIDES the preset's type. "
                    "Availability shifts and is per-type: the measurement "
                    "preset asks for 8 machines, and c6525-25g has repeatedly "
                    "had only ~4 free, which fails mapping outright -- check "
                    "the cluster status page for a type with 8 free before "
                    "instantiating. Use hw_type_custom for anything not "
                    "listed. One homogeneous type per comparison series.")
pc.defineParameter(
    "require_flash_storage", "Refuse to build if the storage hardware has no flash",
    portal.ParameterType.BOOLEAN, False,
    longDescription="Write capacity is fsync-bound: 19.5 qps measured on "
                    "7200RPM spindles against ~3,500 on SATA SSD, same code "
                    "and MORE cores. Set this for a write or overload "
                    "campaign so a spindle-backed cluster is refused at "
                    "instantiation rather than discovered after an hour of "
                    "measurement. Leave it off for read-side work, where "
                    "HDD nodes are perfectly good and far easier to get.")
pc.defineParameter(
    "load_hw_type", "Load-driver hardware type (lg + fe hosts; empty = same as the rest)",
    portal.ParameterType.STRING, "",
    longDescription="Hardware for the load-generating roles. Their bottleneck "
                    "is SINGLE-THREAD speed, not cores: one pacer goroutine "
                    "per agent fires the whole schedule for its share, "
                    "measured at about 2500 qps per pacer on 2.6GHz Skylake. "
                    "Prefer a high clock and a recent generation; a 28-core "
                    "2.0GHz Haswell offers less load than a 32-core 2.6GHz "
                    "Skylake despite the core count looking similar.")
pc.defineParameter(
    "ctl_hw_type", "Control/observer hardware type (empty = same as the rest)",
    portal.ParameterType.STRING, "",
    longDescription="The observer generates no load and runs the samplers, "
                    "Prometheus and Grafana. It is the cheapest role to place: "
                    "give it whatever is plentiful.")
pc.defineParameter(
    "storage_hw_type", "Storage-host hardware type (leave empty = same as the rest)",
    portal.ParameterType.STRING, STORAGE_HW_DEFAULT,
    longDescription="Hardware for the db hosts ONLY. Write capacity is "
                    "fsync-bound, so the log device -- not the core count -- "
                    "decides it: 19.5 qps on a 7200RPM disk against thousands "
                    "on flash, same code, more cores. Put the scarce fast "
                    "machines here and commodity ones everywhere else. Three "
                    "scarce machines also map far more often than eight: a "
                    "request for eight of a type with four free fails "
                    "outright. Empty means every role uses the same type.")
pc.defineParameter(
    "storage_hw_type_custom", "Custom storage hardware type (overrides the field above)",
    portal.ParameterType.STRING, "",
    longDescription="Escape hatch for a storage type not in the list.")
pc.defineParameter(
    "hw_type_custom", "Custom hardware type (overrides the list)",
    portal.ParameterType.STRING, "",
    longDescription="Escape hatch for new or unlisted node types; it beats "
                    "both the dropdown and the preset. One experimental "
                    "interface suffices. The smoke suite re-validates any "
                    "substitution in minutes.")
pc.defineParameter(
    "disk_image", "Disk image URN", portal.ParameterType.STRING, GOLDEN_IMAGE,
    longDescription="Defaults to the golden image (~15-minute redeploy). Use "
                    "BASE_IMAGE from the profile source to rebuild from "
                    "scratch. Not overridden by presets.")
pc.defineParameter(
    "data_size", "Data blockstore per storage host",
    portal.ParameterType.STRING, "600GB",
    longDescription="Mounted at /mnt/data. This does NOT gate access to the "
                    "hardware -- the node is yours and every physical disk is "
                    "present and writable as a raw device either way. What it "
                    "gates is what CloudLab automatically carves, formats and "
                    "mounts for you; unrequested space simply stays "
                    "unpartitioned. Sized past RAM (128GB on c6525-25g) so a "
                    "working set can exceed the page cache, which the "
                    "cache-failure scenario needs. Checked at MAPPING time "
                    "against free space on the node's spare disks: if "
                    "instantiation fails complaining about space, lower it. "
                    "Empty skips the blockstore (data lands on /local -- "
                    "smoke only).")
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
if params.preset != "custom":
    if params.preset not in PRESETS:
        pc.reportError(portal.ParameterError(
            "unknown preset %r" % params.preset, ["preset"]))
    else:
        cfg.update(PRESETS[params.preset])

# Explicit hardware intent outranks the preset, and must be applied AFTER it.
# Until 2026-08-25 this ran BEFORE the preset update, so measurement and
# submission -- the only presets that name a type -- silently forced
# c6525-25g no matter what the form said, including hw_type_custom. The
# symptom was a mapping failure blaming a type the user had not chosen.
if params.hw_type.strip():
    cfg["hw_type"] = params.hw_type.strip()
if params.hw_type_custom.strip():
    cfg["hw_type"] = params.hw_type_custom.strip()
if not cfg["hw_type"]:
    cfg["hw_type"] = DEFAULT_HW
# Storage hardware follows the same precedence, and falls back to the
# cluster-wide type so a homogeneous request still works unchanged.
if params.storage_hw_type.strip():
    cfg["storage_hw_type"] = params.storage_hw_type.strip()
if params.storage_hw_type_custom.strip():
    cfg["storage_hw_type"] = params.storage_hw_type_custom.strip()
if params.load_hw_type.strip():
    cfg["load_hw_type"] = params.load_hw_type.strip()
if params.ctl_hw_type.strip():
    cfg["ctl_hw_type"] = params.ctl_hw_type.strip()
# Every group falls back to the cluster-wide type, so naming none of them
# gives the homogeneous request unchanged.
for _g in ("storage_hw_type", "load_hw_type", "ctl_hw_type"):
    if not cfg.get(_g):
        cfg[_g] = cfg["hw_type"]

# Both types must live in the SAME cluster. Types we do not recognise are
# left alone: an unknown type is a new one, not necessarily a remote one.
_seen = {}
for _f in ("hw_type", "storage_hw_type", "load_hw_type", "ctl_hw_type"):
    _cl = HW_CLUSTER.get(cfg[_f])
    if _cl:
        _seen.setdefault(_cl, []).append("%s=%s" % (_f, cfg[_f]))
_sdisk = HW_DISK.get(cfg["storage_hw_type"])
if params.require_flash_storage and _sdisk == "hdd":
    pc.reportError(portal.ParameterError(
        "storage hardware %s has 7200RPM spindles, and you asked for flash. "
        "Write capacity is fsync-bound: 19.5 qps measured on this class of "
        "disk against ~3,500 on SATA SSD, on identical code with more cores. "
        "At Clemson every currently-free type is HDD-only (c6320, c8220, "
        "c8220x, c4130, ibm8335); the flash types there -- r6615, r650, "
        "r7525, c6420 -- are usually at zero. Utah's c6525-25g (SATA SSD) is "
        "the reliable flash option. Unset this parameter to build an "
        "HDD cluster deliberately, which is the right choice for read-side "
        "and overload work." % cfg["storage_hw_type"],
        ["storage_hw_type", "require_flash_storage"]))
if len(_seen) > 1:
    pc.reportError(portal.ParameterError(
        "hardware types span %d CloudLab clusters (%s). One experiment CAN "
        "span aggregates, but the LAN between them becomes a stitched "
        "wide-area link -- tens of milliseconds RTT against ~0.1ms locally -- "
        "so every raft commit would pay WAN latency and the write capacity "
        "measured would be the network's, not the storage engine's. Pick every "
        "type from one cluster."
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
    # Per-ROLE hardware. Only the storage role gets the scarce type; asking
    # for three of it instead of eight is what makes the request mappable
    # when the fast pool is nearly full.
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
