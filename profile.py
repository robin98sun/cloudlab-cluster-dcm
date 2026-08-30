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

# Per-group hardware. Each group falls back to the cluster-wide hw_type, so
# naming none of them gives a homogeneous request unchanged.
STORAGE_HW_DEFAULT = ""

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

# The hardware list, defined once and offered by EVERY hardware parameter.
# Four separate copies would drift, and a per-group field that is a free-text
# box while the cluster-wide one is a dropdown is the same list twice with
# different affordances.
HW_TYPES = [
    ("c6525-25g", "Utah c6525-25g: AMD 7302P 16c @3.00GHz, 128GB, 2x480GB SATA SSD"),
    ("c6525-100g", "Utah c6525-100g: AMD 7402P 24c @2.80GHz, 128GB, 2x1.6TB NVMe"),
    ("c6620", "Utah c6620: Xeon Gold 5512U 28c @2.1GHz, 128GB, 2x800GB NVMe"),
    ("d6515", "Utah d6515: AMD 7452 32c @2.35GHz, 128GB, 2x480GB SATA SSD"),
    ("d7615", "Utah d7615: AMD 9354P 32c @3.25GHz, 192GB, NVMe"),
    ("c6320", "Clemson c6320: 2x E5-2683v3 28c @2.0GHz, 256GB, 2x1TB HDD"),
    ("c6420", "Clemson c6420: 2x Xeon Gold 6142 32c @2.6GHz, 384GB, 2x1TB HDD"),
    ("c8220", "Clemson c8220: 2x E5-2660v2 20c @2.2GHz, 256GB, 2x1TB HDD"),
    ("c8220x", "Clemson c8220x: 2x E5-2660v2 20c @2.2GHz, 256GB, 20x HDD"),
    ("c4130", "Clemson c4130: 2x E5-2680v3 24c @2.5GHz, 256GB, 2x1TB HDD, 2x K40m"),
    ("r650", "Clemson r650: 2x Xeon Plat 8360Y 72c @2.4GHz, 256GB, SATA SSD + NVMe"),
    ("r6615", "Clemson r6615: AMD 9354P 32c @3.25GHz, 192GB, 2x800GB NVMe"),
    ("r7525", "Clemson r7525: 2x AMD 7542 64c @2.9GHz, 512GB, 2TB HDD"),
]


def hw_choices(first_label):
    """The same list, with a parameter-appropriate empty option."""
    return [("", first_label)] + HW_TYPES


PRESETS = {
    "smoke": dict(num_fe_hosts=1, num_db_hosts=1, num_lg_hosts=0,
                  fe_instances=3, data_size="20GB"),
    "full": dict(num_fe_hosts=1, num_db_hosts=3, num_lg_hosts=0,
                 fe_instances=3, data_size="600GB"),
    # measurement-v2: the measurement-valid topology (24 machines).
    # Sized by MEASUREMENT, not guesswork: on NVMe-class storage the load
    # tier is the bottleneck long before the database is. Three 4-core
    # storage pods needed ~290k read qps to saturate, and a load host
    # paces cleanly at about one agent per physical core x ~2.4k qps
    # (~43k/host on 20-core c8220s; 23 agents/host already failed the pace
    # gate at 2.83% late). Four load hosts therefore topped out at ~180k --
    # 55% of pod CPU -- and the "capacity" that fleet measures is its own.
    # Ten LG hosts give ~430k qps of honest pacing, enough to saturate the
    # storage tier with margin; ten FE hosts keep the front tier from
    # becoming the same kind of hidden ceiling when requests flow through
    # it. 10:10:3 is the ratio that lets 3 NVMe storage nodes actually be
    # the thing measured.
    "measurement": dict(num_fe_hosts=10, num_db_hosts=3, num_lg_hosts=10,
                        fe_instances=1, hw_type="c6525-25g",
                        data_size="600GB", client_bw=0, backend_bw=0),
    # Different hardware per group, in one experiment. Same 10:10:3 sizing;
    # the load and FE tiers are commodity on purpose -- pacing needs cores,
    # not fast storage -- but there must be ENOUGH of them.
    "measurement-het": dict(num_fe_hosts=10, num_db_hosts=3, num_lg_hosts=10,
                            fe_instances=1, hw_type="c6420",
                            storage_hw_type="r6615", load_hw_type="r7525",
                            ctl_hw_type="c6420", data_size="600GB",
                            client_bw=0, backend_bw=0),
    # Freeze everything that defines the reported configuration. Pin
    # disk_image here too once the submission-era golden image exists.
    "submission": dict(num_fe_hosts=10, num_db_hosts=3, num_lg_hosts=10,
                       fe_instances=1, hw_type="c6525-25g", data_size="600GB",
                       client_bw=0, backend_bw=0),
}

pc = portal.Context()

pc.defineParameter(
    "preset", "Configuration preset", portal.ParameterType.STRING, "smoke",
    legalValues=[("smoke", "smoke: 3 machines, 1 db host (plumbing-valid)"),
                 ("full", "full: 5 machines, 3 db hosts (plumbing-valid)"),
                 ("measurement", "measurement: 8 machines, 3 FE hosts, dedicated LG"),
                 ("measurement-het", "measurement-het: 8 machines, per-group "
                  "hardware types"),
                 ("submission", "submission: frozen measurement-scale bindings"),
                 ("custom", "custom: use the individual fields below")],
    longDescription="Anything other than 'custom' overrides the individual "
                    "fields it defines (see the profile source for exact "
                    "bindings). Presets are versioned with the repository, so "
                    "every result bundle can name its configuration by commit.")
pc.defineParameter(
    "num_fe_hosts", "Frontend hosts (custom preset)",
    portal.ParameterType.INTEGER, 10,
    longDescription="Each runs fe_instances FE+testbed pods; one host already "
                    "preserves the multi-upstream property, but front-tier "
                    "throughput must scale with the load tier or it becomes "
                    "the hidden ceiling. Default 10 per 3 storage hosts.")
pc.defineParameter(
    "num_db_hosts", "Storage hosts (custom preset)",
    portal.ParameterType.INTEGER, 1,
    longDescription="One replica pod per host. 1 for smoke, 3 for Raft. "
                    "2 is refused.")
pc.defineParameter(
    "num_lg_hosts", "Dedicated load-generator hosts (custom preset)",
    portal.ParameterType.INTEGER, 10,
    longDescription="Open-loop pacing holds about one agent per physical "
                    "core at ~2.4k qps each (~43k/host on 20-core machines), "
                    "and a fleet that cannot outrun the storage tier measures "
                    "ITSELF -- four hosts topped out at 55%% of pod CPU and "
                    "the pace gate voided everything above it. Default 10 per "
                    "3 NVMe storage hosts. 0 runs load generation on ctl1 "
                    "(smoke only).")
pc.defineParameter(
    "fe_instances", "FE pods per frontend host (custom preset)",
    portal.ParameterType.INTEGER, 3,
    longDescription="Ports 8081, 8082, ... Three is the minimum for the "
                    "distributed property: several independent "
                    "admission points enforcing one shared budget.")
pc.defineParameter(
    "hw_type", "Hardware type", portal.ParameterType.STRING, "",
    legalValues=hw_choices(
        "preset default -- leave to let the preset decide"),
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
    "load_hw_type", "Load-driver hardware type (lg + fe hosts; empty = same as the rest)",
    portal.ParameterType.STRING, "",
    legalValues=hw_choices("same as the cluster-wide type"),
    longDescription="Hardware for the lg and fe hosts. Empty means the "
                    "cluster-wide type.")
pc.defineParameter(
    "ctl_hw_type", "Control/observer hardware type (empty = same as the rest)",
    portal.ParameterType.STRING, "",
    legalValues=hw_choices("same as the cluster-wide type"),
    longDescription="Hardware for ctl1. Empty means the cluster-wide type.")
pc.defineParameter(
    "storage_hw_type", "Storage-host hardware type (leave empty = same as the rest)",
    portal.ParameterType.STRING, STORAGE_HW_DEFAULT,
    legalValues=hw_choices("same as the cluster-wide type"),
    longDescription="Hardware for the db hosts. Empty means the "
                    "cluster-wide type.")
pc.defineParameter(
    "storage_hw_type_custom", "Custom storage hardware type (overrides the field above)",
    portal.ParameterType.STRING, "",
    longDescription="Any type not in the list.")
pc.defineParameter(
    "load_hw_type_custom", "Custom load-driver hardware type (overrides the field above)",
    portal.ParameterType.STRING, "",
    longDescription="Any type not in the list.")
pc.defineParameter(
    "ctl_hw_type_custom", "Custom control hardware type (overrides the field above)",
    portal.ParameterType.STRING, "",
    longDescription="Any type not in the list.")
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
if params.load_hw_type_custom.strip():
    cfg["load_hw_type"] = params.load_hw_type_custom.strip()
if params.ctl_hw_type.strip():
    cfg["ctl_hw_type"] = params.ctl_hw_type.strip()
if params.ctl_hw_type_custom.strip():
    cfg["ctl_hw_type"] = params.ctl_hw_type_custom.strip()
# Every group falls back to the cluster-wide type, so naming none of them
# gives the homogeneous request unchanged.
for _g in ("storage_hw_type", "load_hw_type", "ctl_hw_type"):
    if not cfg.get(_g):
        cfg[_g] = cfg["hw_type"]

# Every type that PLACES A NODE must live in the same cluster. The check
# runs over the three role types only: once every role has an explicit
# type, the cluster-wide hw_type places nothing -- it is a fallback
# source, already propagated above. Including it compared the preset's
# default (utah c6525-25g) against a fully-Clemson selection and refused
# a request that named no utah node at all.
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
