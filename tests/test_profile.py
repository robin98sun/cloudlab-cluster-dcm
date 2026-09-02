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
    for line in p.stdout.splitlines():
        f = line.split()
        if len(f) >= 4 and f[0] not in ("node", "LANs:"):
            nodes[f[0]] = {"hw": f[1], "lan": f[2], "store": f[3]}
        elif line.strip().startswith("LANs:"):
            lans.append(line.split(":", 1)[1].strip())
    return nodes, lans


def with_(**kw):
    d = dict(THREE_DB)
    d.update(kw)
    return d


class Sizing(unittest.TestCase):
    def test_one_storage_host_is_a_valid_request(self):
        # 1 is a complete testbed, not a degenerate case
        nodes, lans = request_for({"hw_type": "c6420", "num_db_hosts": 1,
                                   "num_fe_hosts": 1, "num_lg_hosts": 0})
        self.assertIn("db1", nodes)
        self.assertNotIn("db2", nodes)
        self.assertEqual(len(lans), 1)

    def test_two_storage_hosts_are_refused(self):
        with self.assertRaises(AssertionError) as e:
            request_for(with_(num_db_hosts=2))
        self.assertIn("Two storage hosts", str(e.exception))

    def test_zero_storage_hosts_are_refused(self):
        with self.assertRaises(AssertionError):
            request_for(with_(num_db_hosts=0))

    def test_zero_frontend_hosts_are_refused(self):
        with self.assertRaises(AssertionError):
            request_for(with_(num_fe_hosts=0))

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

    def test_load_type_can_be_set_alone(self):
        nodes, _ = request_for(with_(num_lg_hosts=1, load_hw_type="r7525"))
        self.assertEqual(nodes["db1"]["hw"], "c6420")
        self.assertEqual(nodes["lg1"]["hw"], "r7525")
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

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

    def test_types_split_across_clusters_are_refused(self):
        with self.assertRaises(AssertionError) as e:
            request_for(with_(storage_hw_type="c6525-25g"))
        self.assertIn("Pick every type from one cluster", str(e.exception))

    def test_any_group_crossing_clusters_is_refused(self):
        with self.assertRaises(AssertionError) as e:
            request_for(with_(num_lg_hosts=1, load_hw_type="c6525-25g"))
        self.assertIn("Pick every type from one cluster", str(e.exception))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
