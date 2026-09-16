"""Execute the CloudLab profile locally and check what it requests.

geni-lib does not install on a modern Python, so a profile was previously
unverifiable outside the portal -- which is how a preset came to silently
override every hardware choice the form offered. tests/genistub is a minimal
stand-in for geni.portal and geni.rspec.pg: enough to run the profile and
record the resulting request.

The presets and the hardware dropdowns are gone (see profile.py). What
remains is one flat set of fields, so these tests drive those fields
directly -- which is also what the portal form now does.

    python3 tests/test_profile.py
"""
import json
import os
import subprocess
import sys
import unittest
from shutil import which as _which

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STUB = os.path.join(HERE, "genistub")

# A 3-storage-host request, spelled out. Every test that needs a replicated
# cluster starts from this rather than from a named preset.
THREE_DB = {"hw_type": "c6420", "num_db_hosts": 3, "num_fe_hosts": 1,
            "num_lg_hosts": 0}


def request_for(params):
    env = dict(os.environ, PYTHONPATH=STUB, PROFILE_PARAMS=json.dumps(params))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "profile.py")],
                       capture_output=True, text=True, env=env, timeout=60)
    if p.returncode != 0:
        raise AssertionError("profile failed: %s" % (p.stderr[-400:]))
    nodes, lans = {}, []
    cmds = {}
    for line in p.stdout.splitlines():
        f = line.split()
        if f[:1] == ["CMD"]:
            cmds[f[1]] = " ".join(f[2:])
            continue
        if len(f) >= 4 and f[0] not in ("node", "LANs:"):
            nodes[f[0]] = {"hw": f[1], "lan": f[2], "store": f[3],
                           "image": f[4] if len(f) >= 5 else "-"}
        elif line.strip().startswith("LANs:"):
            lans.append(line.split(":", 1)[1].strip())
    for _n, _c in cmds.items():
        if _n in nodes:
            nodes[_n]["cmd"] = _c
    return nodes, lans


def with_(**kw):
    d = dict(THREE_DB)
    d.update(kw)
    return d


class ControlPlanePlacement(unittest.TestCase):
    """The control plane carries no measured load, so it need not own a
    machine. These pin WHICH node runs it and how agents are told."""

    def test_dedicated_is_the_default_and_unchanged(self):
        nodes, _ = request_for(with_())
        self.assertIn("ctl1", nodes)
        self.assertIn("bootstrap.sh ctl", nodes["ctl1"]["cmd"])
        self.assertIn("--server-node ctl1", nodes["ctl1"]["cmd"])
        # workers point at it and do NOT run a server
        self.assertIn("--server-node ctl1", nodes["db1"]["cmd"])
        self.assertNotIn("--server ", nodes["db1"]["cmd"])

    def test_co_located_puts_the_server_on_db1_and_drops_ctl1(self):
        nodes, _ = request_for(with_(dedicated_ctl=False))
        self.assertNotIn("ctl1", nodes)
        cmd = nodes["db1"]["cmd"]
        self.assertIn("bootstrap.sh db", cmd)      # keeps its own role
        self.assertIn("--server", cmd)             # and runs the control plane
        self.assertIn("--fe-hosts", cmd)           # with the control args
        for other in ("db2", "db3", "fe1"):
            self.assertIn("--server-node db1", nodes[other]["cmd"])
            self.assertNotIn("--server ", nodes[other]["cmd"])

    def test_co_location_falls_back_through_the_roles(self):
        # No storage -> frontend; no frontend either -> load; none at all ->
        # a dedicated ctl1, because an empty request answers nothing.
        n, _ = request_for(with_(dedicated_ctl=False, num_db_hosts=0))
        self.assertIn("--server", n["fe1"]["cmd"])
        n, _ = request_for(with_(dedicated_ctl=False, num_db_hosts=0,
                                 num_fe_hosts=0, num_lg_hosts=2))
        self.assertIn("--server", n["lg1"]["cmd"])
        n, _ = request_for(with_(dedicated_ctl=False, num_db_hosts=0,
                                 num_fe_hosts=0, num_lg_hosts=0))
        self.assertIn("ctl1", n)

    def test_co_located_onto_a_custom_slot(self):
        n, _ = request_for(with_(dedicated_ctl=False, num_db_hosts=0,
                                 num_fe_hosts=0, num_lg_hosts=0,
                                 cm3_hw_type="r6615", cm7_hw_type="r650"))
        self.assertNotIn("ctl1", n)
        self.assertIn("--server", n["cm3"]["cmd"])   # lowest slot wins
        self.assertNotIn("--server ", n["cm7"]["cmd"])


class PythonTwo(unittest.TestCase):
    """The portal runs this profile under PYTHON 2. These tests exist because
    the suite did not, and a `min(..., default=None)` shipped that parsed
    fine, passed all 55 tests under python3, and made the profile unreadable
    on CloudLab with `TypeError: min() got unexpected keyword argument`.

    Running the profile under python3 only can never catch that class of bug.
    """

    PY2 = next((p for p in ("python2", "python2.7",
                            "/usr/bin/python2.7",
                            os.path.expanduser(
                                "~/Dev/Libraries/pypy2/bin/python2"))
                if _which(p)), None)

    def test_profile_executes_under_python2(self):
        if not self.PY2:
            self.skipTest("no python2 here; the static scan below still runs")
        for params in ({}, {"dedicated_ctl": False},
                       {"dedicated_ctl": False, "num_db_hosts": 0,
                        "num_fe_hosts": 0, "num_lg_hosts": 0},
                       {"num_db_hosts": 4, "storage_hw_type": "r6615,r6525"}):
            env = dict(os.environ, PYTHONPATH=STUB,
                       PROFILE_PARAMS=json.dumps(params))
            p = subprocess.run([self.PY2, os.path.join(ROOT, "profile.py")],
                               capture_output=True, text=True, env=env,
                               timeout=60)
            self.assertEqual(p.returncode, 0,
                             "py2 failed for %s:\n%s" % (params, p.stderr[-400:]))

    def test_no_python3_only_constructs(self):
        # Runs everywhere, including machines with no python2 at all. Each
        # pattern is something that parses or passes under python3 and breaks
        # under the interpreter the portal actually uses.
        #
        # COMMENTS AND DOCSTRINGS ARE STRIPPED FIRST. The first version of
        # this test flagged its own explanatory comment about
        # min(..., default=) -- a scanner that cannot tell code from prose
        # reports the fix as the bug.
        import io as _io
        import re as _re
        import tokenize as _tok

        with open(os.path.join(ROOT, "profile.py")) as fh:
            src = fh.read()
        pieces = []
        for tk in _tok.generate_tokens(_io.StringIO(src).readline):
            if tk.type in (_tok.COMMENT, _tok.STRING):
                continue
            pieces.append(tk.string)
        code = " ".join(pieces)

        for label, pat in (
                ("min/max with default=", r"\b(?:min|max)\s*\([^)]*\bdefault\s*="),
                ("walrus :=", r":="),
                ("nonlocal", r"\bnonlocal\b"),
                ("subprocess.run", r"\bsubprocess\s*\.\s*run\b"),
                ("yield from", r"\byield\s+from\b"),
        ):
            m = _re.search(pat, code)
            self.assertIsNone(m, "python3-only construct in profile.py (%s)"
                                 % label)
        # f-strings are a syntax error under py2, so a successful py2 compile
        # covers them; check the source text anyway for a clearer message.
        self.assertIsNone(_re.search(r"""\bf["']""", src),
                          "f-string in profile.py; the portal runs python2")


class Sizing(unittest.TestCase):
    def test_one_storage_host_is_a_valid_request(self):
        # 1 is a complete testbed, not a degenerate case
        nodes, lans = request_for({"hw_type": "c6420", "num_db_hosts": 1,
                                   "num_fe_hosts": 1, "num_lg_hosts": 0})
        self.assertIn("db1", nodes)
        self.assertNotIn("db2", nodes)
        self.assertEqual(len(lans), 1)

    def test_two_storage_hosts_are_allowed_so_a_tier_can_grow(self):
        # Formerly refused, on the correct observation that 2 tolerates no
        # failures. But refusing it also made 1 -> 2 -> 3 impossible, and
        # growing a tier one free machine at a time is the whole reason
        # storage_hw_type takes a list. The arithmetic now lives in the
        # field description; the count is the operator's to choose.
        nodes, lans = request_for(with_(num_db_hosts=2))
        self.assertIn("db1", nodes)
        self.assertIn("db2", nodes)
        self.assertNotIn("db3", nodes)
        self.assertEqual(len(lans), 1)

    def test_any_storage_count_is_allowed_and_may_be_heterogeneous(self):
        # Five hosts of four different types: the case the portal form
        # blocked, and the one an operator hits when absorbing whatever a
        # cluster has free.
        nodes, _ = request_for(with_(
            num_db_hosts=5,
            storage_hw_type="r6615,r6525,r6615,c6420,r650"))
        self.assertEqual([nodes["db%d" % k]["hw"] for k in range(1, 6)],
                         ["r6615", "r6525", "r6615", "c6420", "r650"])

    def test_a_storage_list_shorter_than_the_count_cycles(self):
        # Two types over four hosts, alternating. Formerly refused; the
        # refusal was also the only thing preventing an IndexError at
        # node-build time, so _expand_hw had to become total, not just
        # permissive.
        nodes, _ = request_for(with_(num_db_hosts=4,
                                     storage_hw_type="r6615,r6525"))
        self.assertEqual([nodes["db%d" % k]["hw"] for k in (1, 2, 3, 4)],
                         ["r6615", "r6525", "r6615", "r6525"])

    def test_zero_storage_hosts_place_no_storage(self):
        nodes, _ = request_for(with_(num_db_hosts=0))
        self.assertNotIn("db1", nodes)
        self.assertIn("ctl1", nodes)

    def test_zero_frontend_hosts_place_no_frontend(self):
        nodes, lans = request_for(with_(num_fe_hosts=0))
        self.assertNotIn("fe1", nodes)
        self.assertIn("ctl1", nodes)
        self.assertIn("db1", nodes)
        self.assertEqual(len(lans), 1)

    def test_host_counts_are_honoured(self):
        nodes, lans = request_for({"hw_type": "c6420", "num_db_hosts": 3,
                                   "num_fe_hosts": 10, "num_lg_hosts": 10})
        self.assertEqual(len([k for k in nodes if k.startswith("db")]), 3)
        self.assertEqual(len([k for k in nodes if k.startswith("fe")]), 10)
        self.assertEqual(len([k for k in nodes if k.startswith("lg")]), 10)
        # 10 + 10 + 3 + ctl1, all on ONE experiment LAN: separate experiments
        # would have to talk over the control network.
        self.assertEqual(len(lans), 1)
        self.assertIn("24 ifaces", lans[0])


class PerRoleHardware(unittest.TestCase):
    """One experiment may request different hardware per node -- which is what
    makes a scarce type usable at all when only three machines of it exist."""

    def test_every_role_can_take_its_own_type(self):
        nodes, lans = request_for(with_(num_lg_hosts=1,
                                        storage_hw_type="r6615",
                                        load_hw_type="r7525",
                                        ctl_hw_type="c6420"))
        self.assertEqual({v["hw"] for k, v in nodes.items()
                          if k.startswith("db")}, {"r6615"})
        self.assertEqual({v["hw"] for k, v in nodes.items()
                          if k.startswith(("lg", "fe"))}, {"r7525"})
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")
        self.assertEqual(len(lans), 1)      # still ONE experiment LAN

    def test_the_scarce_type_lands_only_on_storage(self):
        nodes, _ = request_for(with_(num_db_hosts=3, storage_hw_type="r6615",
                                     load_hw_type="r7525",
                                     ctl_hw_type="c6420"))
        for k, v in nodes.items():
            if not k.startswith("db"):
                self.assertNotEqual(v["hw"], "r6615", k)

    def test_each_role_falls_back_to_the_cluster_type(self):
        nodes, _ = request_for(with_())
        self.assertEqual({v["hw"] for v in nodes.values()}, {"c6420"})

    def test_storage_type_can_be_set_alone(self):
        nodes, _ = request_for(with_(storage_hw_type="c6320"))
        self.assertEqual(nodes["db1"]["hw"], "c6320")
        self.assertEqual(nodes["fe1"]["hw"], "c6420")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_storage_types_may_differ_per_host(self):
        # The 3-node storage cluster is often only reachable by taking the
        # two of one type and the one of another that a cluster has free.
        nodes, _ = request_for(with_(num_db_hosts=3,
                                     storage_hw_type="r6525,r6525,r6615"))
        self.assertEqual(nodes["db1"]["hw"], "r6525")
        self.assertEqual(nodes["db2"]["hw"], "r6525")
        self.assertEqual(nodes["db3"]["hw"], "r6615")

    def test_one_storage_type_still_applies_to_every_host(self):
        nodes, _ = request_for(with_(num_db_hosts=3, storage_hw_type="c6320"))
        self.assertEqual({nodes["db%d" % k]["hw"] for k in (1, 2, 3)},
                         {"c6320"})

    def test_a_short_storage_list_cycles_to_fill_the_hosts(self):
        nodes, _ = request_for(with_(num_db_hosts=3,
                                     storage_hw_type="r6525,r6615"))
        self.assertEqual([nodes["db%d" % k]["hw"] for k in (1, 2, 3)],
                         ["r6525", "r6615", "r6525"])

    def test_storage_list_entries_are_trimmed(self):
        nodes, _ = request_for(with_(num_db_hosts=3,
                                     storage_hw_type=" r6525 , r6525 , r6615 "))
        self.assertEqual(nodes["db3"]["hw"], "r6615")

    def test_a_storage_list_crossing_clusters_is_accepted(self):
        nodes, _ = request_for(with_(num_db_hosts=3,
                                     storage_hw_type="r6525,r6525,c6525-25g"))
        self.assertEqual([nodes["db%d" % k]["hw"] for k in (1, 2, 3)],
                         ["r6525", "r6525", "c6525-25g"])

    def test_load_types_may_differ_per_host(self):
        # Growing a RUNNING experiment: keep the entries the existing hosts
        # already have and append the new type, so Modify adds hosts rather
        # than remapping the ones already provisioned.
        nodes, _ = request_for(with_(num_lg_hosts=4,
                                     load_hw_type="r650,r650,r6615,r6615"))
        self.assertEqual(nodes["lg1"]["hw"], "r650")
        self.assertEqual(nodes["lg2"]["hw"], "r650")
        self.assertEqual(nodes["lg3"]["hw"], "r6615")
        self.assertEqual(nodes["lg4"]["hw"], "r6615")

    def test_fe_types_may_differ_per_host(self):
        nodes, _ = request_for(with_(num_fe_hosts=3,
                                     fe_hw_type="r650,r650,r6615"))
        self.assertEqual(nodes["fe1"]["hw"], "r650")
        self.assertEqual(nodes["fe3"]["hw"], "r6615")

    def test_one_load_type_still_applies_to_every_host(self):
        nodes, _ = request_for(with_(num_lg_hosts=3, load_hw_type="c6320"))
        self.assertEqual({nodes["lg%d" % i]["hw"] for i in (1, 2, 3)},
                         {"c6320"})

    def test_a_short_load_list_cycles_to_fill_the_hosts(self):
        # Two types across three hosts: r650, r6615, r650.
        nodes, _ = request_for(with_(num_lg_hosts=3,
                                     load_hw_type="r650,r6615"))
        self.assertEqual([nodes["lg%d" % i]["hw"] for i in (1, 2, 3)],
                         ["r650", "r6615", "r650"])

    def test_a_long_fe_list_is_truncated_to_the_host_count(self):
        nodes, _ = request_for(with_(num_fe_hosts=2,
                                     fe_hw_type="r650,r6615,c6420,r7525"))
        self.assertEqual([nodes["fe1"]["hw"], nodes["fe2"]["hw"]],
                         ["r650", "r6615"])
        self.assertNotIn("fe3", nodes)

    def test_a_load_list_crossing_clusters_is_accepted(self):
        nodes, _ = request_for(with_(num_lg_hosts=2,
                                     load_hw_type="r650,c6525-25g"))
        self.assertEqual([nodes["lg1"]["hw"], nodes["lg2"]["hw"]],
                         ["r650", "c6525-25g"])

    def test_mixed_load_and_fe_lists_place_every_host(self):
        # The 2026-09-07 growth case: an all-r650 testbed gaining r6615
        # load and frontend hosts without touching the r650 ones.
        nodes, _ = request_for(with_(num_db_hosts=3, num_fe_hosts=6,
                                     num_lg_hosts=7,
                                     storage_hw_type="r650",
                                     load_hw_type="r650,r650,r650,r650,r650,"
                                                  "r6615,r6615",
                                     fe_hw_type="r650,r650,r650,r650,"
                                                "r6615,r6615"))
        self.assertEqual({nodes["db%d" % k]["hw"] for k in (1, 2, 3)},
                         {"r650"})
        self.assertEqual(nodes["lg5"]["hw"], "r650")
        self.assertEqual(nodes["lg6"]["hw"], "r6615")
        self.assertEqual(nodes["fe4"]["hw"], "r650")
        self.assertEqual(nodes["fe6"]["hw"], "r6615")

    def test_load_type_can_be_set_alone(self):
        nodes, _ = request_for(with_(num_lg_hosts=1, load_hw_type="r7525"))
        self.assertEqual(nodes["db1"]["hw"], "c6420")
        self.assertEqual(nodes["lg1"]["hw"], "r7525")
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_frontend_type_can_be_set_alone(self):
        """fe and lg used to share one field. A cluster often has plenty of
        one type and little of the other, and pinning both to the same type
        fails the whole allocation when either runs short."""
        nodes, _ = request_for(with_(num_lg_hosts=1, fe_hw_type="r7525"))
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["lg1"]["hw"], "c6420")   # unchanged
        self.assertEqual(nodes["db1"]["hw"], "c6420")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_frontend_and_load_can_differ(self):
        nodes, _ = request_for(with_(num_lg_hosts=1, num_fe_hosts=1,
                                     load_hw_type="c6320",
                                     fe_hw_type="r7525"))
        self.assertEqual(nodes["lg1"]["hw"], "c6320")
        self.assertEqual(nodes["fe1"]["hw"], "r7525")

    def test_an_unset_frontend_type_follows_the_load_type(self):
        """The compatibility rule: before the split these shared a field, so
        an unset fe type must land wherever load_hw_type points -- NOT on the
        cluster-wide type, which would silently change existing invocations."""
        nodes, _ = request_for(with_(num_lg_hosts=1, load_hw_type="r7525"))
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["lg1"]["hw"], "r7525")

    def test_control_type_can_be_set_alone(self):
        nodes, _ = request_for(with_(ctl_hw_type="c6320"))
        self.assertEqual(nodes["ctl1"]["hw"], "c6320")
        self.assertEqual(nodes["db1"]["hw"], "c6420")

    def test_only_storage_hosts_get_a_data_blockstore(self):
        nodes, _ = request_for(with_())
        for name, v in nodes.items():
            if name.startswith("db"):
                self.assertIn("/mnt/data", v["store"], name)
            else:
                self.assertEqual(v["store"], "-", name)


class UnlistedTypes(unittest.TestCase):
    """Any type name must be usable, listed here or not.

    The dropdowns could only offer what this file already knew, so every real
    request ended up in the free-text box beside them -- r6525 was not in the
    list. The fields are free text now, and that is the whole mechanism.
    """

    def test_an_unlisted_type_is_accepted_everywhere(self):
        nodes, _ = request_for(with_(num_lg_hosts=1,
                                     storage_hw_type="storage-x",
                                     load_hw_type="load-x",
                                     ctl_hw_type="ctl-x"))
        self.assertEqual(nodes["db1"]["hw"], "storage-x")
        self.assertEqual(nodes["fe1"]["hw"], "load-x")
        self.assertEqual(nodes["lg1"]["hw"], "load-x")
        self.assertEqual(nodes["ctl1"]["hw"], "ctl-x")

    def test_an_unlisted_cluster_wide_type_is_accepted(self):
        nodes, _ = request_for(with_(hw_type="r6525"))
        self.assertEqual(nodes["db1"]["hw"], "r6525")

    def test_whitespace_is_trimmed(self):
        nodes, _ = request_for(with_(storage_hw_type="  c6320  "))
        self.assertEqual(nodes["db1"]["hw"], "c6320")

    def test_an_empty_cluster_type_falls_back_to_the_default(self):
        nodes, _ = request_for({"hw_type": "", "num_db_hosts": 1,
                                "num_fe_hosts": 1, "num_lg_hosts": 0})
        self.assertEqual(nodes["db1"]["hw"], "c6525-25g")


class SingleCluster(unittest.TestCase):
    """One LAN means one CloudLab cluster.

    An experiment can span aggregates, but the LAN between them is a stitched
    wide-area link: tens of milliseconds RTT against ~0.1 ms locally. Raft
    would pay that on every commit and the fsync-bound write capacity this
    project measures would be replaced by a network-bound one.
    """

    def test_types_from_one_cluster_are_accepted(self):
        nodes, _ = request_for(with_(num_lg_hosts=1, storage_hw_type="r6615",
                                     load_hw_type="r7525",
                                     ctl_hw_type="c6420"))
        self.assertEqual(nodes["db1"]["hw"], "r6615")
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_types_split_across_clusters_are_accepted(self):
        nodes, _ = request_for(with_(storage_hw_type="c6525-25g"))
        self.assertEqual(nodes["db1"]["hw"], "c6525-25g")

    def test_one_group_in_another_cluster_is_accepted(self):
        nodes, _ = request_for(with_(num_lg_hosts=1,
                                     load_hw_type="c6525-25g"))
        self.assertEqual(nodes["lg1"]["hw"], "c6525-25g")

    def test_an_unknown_type_is_not_assumed_remote(self):
        # a new hardware type is new, not necessarily elsewhere
        nodes, _ = request_for(with_(storage_hw_type="brand-new-type"))
        self.assertEqual(nodes["db1"]["hw"], "brand-new-type")

    def test_the_clemson_types_in_use_are_classified(self):
        # c8220 runs the load and FE tiers on the live testbed; if it is
        # absent from HW_CLUSTER the cross-cluster guard silently passes
        # on it, which is worse than refusing.
        src = open(os.path.join(ROOT, "profile.py")).read()
        for t in ("c8220", "c8220x", "c4130", "r650", "r6525"):
            self.assertIn('"%s": "clemson"' % t, src, t)


class NoDropdowns(unittest.TestCase):
    """Every field is free text or a number. No parameter offers a fixed list.

    A dropdown here can only ever offer what this file already knows, and
    CloudLab adds hardware faster than this file changes.
    """

    def _choices(self):
        env = dict(os.environ, PYTHONPATH=STUB,
                   PROFILE_PARAMS=json.dumps(THREE_DB),
                   DUMP_PARAM_CHOICES="1")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "profile.py")],
                           capture_output=True, text=True, env=env, timeout=60)
        for line in p.stdout.splitlines():
            if line.startswith("CHOICES "):
                return json.loads(line[len("CHOICES "):])
        raise AssertionError("profile did not dump parameter choices")

    def test_no_parameter_has_a_fixed_list(self):
        for name, choices in self._choices().items():
            self.assertIsNone(choices, "%s is still a dropdown" % name)

    def test_the_preset_parameter_is_gone(self):
        self.assertNotIn("preset", self._choices())

    def test_the_custom_escape_hatches_are_gone(self):
        ch = self._choices()
        for name in ("hw_type_custom", "storage_hw_type_custom",
                     "load_hw_type_custom", "ctl_hw_type_custom"):
            self.assertNotIn(name, ch, "%s should no longer exist" % name)


class PerNodeDiskImage(unittest.TestCase):
    """A CloudLab image is bound to the hardware types it was built for.

    The golden image was baked on c6525-25g at Utah. Ask for a custom host
    of a type it was never built for and the MAPPER refuses the entire
    topology before anything boots:

        *** No possible mapping for cm4
            OS 'aces-project-01-PG0/DCM-dev.db1' does not run on this
            hardware type!

    No node logs that, because no node ran. The cure is a per-node image,
    for the same reason hardware is already per-node.
    """

    def test_a_custom_host_never_refuses_the_topology_by_default(self):
        # The failure this prevents is a MAP-TIME refusal of everything, not a
        # slow boot on one machine. Default cm hosts to an image that maps on
        # any hardware type.
        nodes, _ = request_for(with_(cm1_hw_type="c6420"))
        self.assertNotEqual(nodes["cm1"]["image"], nodes["db1"]["image"],
                            "a custom slot takes arbitrary hardware, so it "
                            "must not default to a type-bound image")
        self.assertIn("UBUNTU", nodes["cm1"]["image"].upper())

    def test_measured_roles_keep_the_golden_image(self):
        nodes, _ = request_for(with_(cm1_hw_type="c6420"))
        golden = nodes["db1"]["image"]
        for name in ("db1", "fe1", "lg1"):
            if name in nodes:
                self.assertEqual(nodes[name]["image"], golden)

    def test_alt_image_applies_to_custom_hosts_only(self):
        nodes, _ = request_for(with_(cm1_hw_type="c6420",
                                     alt_disk_image="STOCK-IMAGE-URN"))
        self.assertEqual(nodes["cm1"]["image"], "STOCK-IMAGE-URN")
        for name, n in nodes.items():
            if not name.startswith("cm"):
                self.assertNotEqual(
                    n["image"], "STOCK-IMAGE-URN",
                    "%s must keep the golden image; only the hosts that "
                    "cannot boot it pay the bake" % name)

    def test_empty_alt_image_is_the_escape_hatch(self):
        # Explicitly empty means "one image everywhere", for the case where
        # the custom host's type IS covered by the golden image and the bake
        # is not worth paying.
        nodes, _ = request_for(with_(cm1_hw_type="c6420", alt_disk_image=""))
        self.assertEqual(nodes["cm1"]["image"], nodes["db1"]["image"])
        self.assertEqual(len({n["image"] for n in nodes.values()}), 1)


class CustomHosts(unittest.TestCase):
    """Ten slots for absorbing isolated idle machines, one at a time.

    The point of the feature is that a RUNNING experiment grows onto
    whatever a cluster happens to have free, so the properties that matter
    are: an unfilled slot costs nothing, a filled one is one node, and a
    node's address depends on its SLOT rather than on how many other slots
    were filled -- otherwise adding cm4 would move cm3 and invalidate
    every config that already named it.
    """

    def test_no_custom_hosts_by_default(self):
        nodes, _ = request_for(THREE_DB)
        self.assertEqual([n for n in nodes if n.startswith("cm")], [])

    def test_a_filled_slot_adds_exactly_one_node(self):
        nodes, _ = request_for(with_(cm1_hw_type="c6420"))
        self.assertIn("cm1", nodes)
        self.assertEqual(nodes["cm1"]["hw"], "c6420")
        self.assertEqual(len([n for n in nodes if n.startswith("cm")]), 1)

    def test_slots_may_be_filled_out_of_order_and_leave_gaps(self):
        # cm2 unavailable when cm3 was grabbed: a gap is normal, not an error
        nodes, _ = request_for(with_(cm1_hw_type="c6420", cm3_hw_type="c6320"))
        self.assertEqual(sorted(n for n in nodes if n.startswith("cm")),
                         ["cm1", "cm3"])

    def test_address_follows_the_slot_not_the_fill_order(self):
        # cm3 is 10.10.1.43 whether or not cm2 was ever filled
        only3, _ = request_for(with_(cm3_hw_type="c6420"))
        both, _ = request_for(with_(cm2_hw_type="c6320", cm3_hw_type="c6420"))
        self.assertEqual(only3["cm3"]["lan"], both["cm3"]["lan"])
        self.assertTrue(only3["cm3"]["lan"].endswith(".43"),
                        only3["cm3"]["lan"])

    def test_custom_hosts_may_each_be_a_different_type(self):
        nodes, _ = request_for(with_(cm1_hw_type="c6420", cm2_hw_type="c6320",
                                     cm3_hw_type="r650"))
        self.assertEqual([nodes["cm%d" % m]["hw"] for m in (1, 2, 3)],
                         ["c6420", "c6320", "r650"])

    def test_a_custom_host_from_another_cluster_is_accepted(self):
        # Was refused. Stitching an aggregate is still the likeliest way to
        # ruin a measurement by accident -- what is free is often free
        # because it is in the other cluster -- but it is now the operator's
        # call, and the warning lives on the hardware fields.
        nodes, _ = request_for(with_(cm1_hw_type="c6525-25g"))
        self.assertEqual(nodes["cm1"]["hw"], "c6525-25g")

    def test_custom_hosts_take_no_blockstore_by_default(self):
        nodes, _ = request_for(with_(cm1_hw_type="c6420"))
        self.assertEqual(nodes["cm1"]["store"], "-")

    def test_custom_hosts_take_a_blockstore_when_asked(self):
        nodes, _ = request_for(with_(cm1_hw_type="c6420", cm_data_size="100GB"))
        self.assertNotEqual(nodes["cm1"]["store"], "-")

    def test_all_ten_slots_can_be_filled(self):
        kw = {"cm%d_hw_type" % m: "c6420" for m in range(1, 11)}
        nodes, _ = request_for(with_(**kw))
        self.assertEqual(len([n for n in nodes if n.startswith("cm")]), 10)
        self.assertTrue(nodes["cm10"]["lan"].endswith(".50"),
                        nodes["cm10"]["lan"])

    def test_custom_hosts_do_not_disturb_the_named_tiers(self):
        # PLACEMENT is the invariant: absorbing a custom host must not move
        # any existing node's address or hardware. It is deliberately not a
        # claim about bootstrap arguments -- the control node is TOLD how
        # many hosts of each role exist, so its --cm-hosts count is supposed
        # to change from 0 to 1 here. Comparing the whole node dict made this
        # test fail on exactly the thing that is meant to happen.
        placement = lambda d: {k: v for k, v in d.items() if k != "cmd"}
        base, _ = request_for(THREE_DB)
        grown, _ = request_for(with_(cm1_hw_type="c6420"))
        for name, spec in base.items():
            self.assertEqual(placement(grown[name]), placement(spec),
                             "%s moved" % name)
        self.assertIn("--cm-hosts 1", grown["ctl1"]["cmd"])
        self.assertIn("--cm-hosts 0", base["ctl1"]["cmd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
