"""Execute the CloudLab profile locally and check what it requests.

geni-lib does not install on a modern Python, so a profile was previously
unverifiable outside the portal -- which is how a preset came to silently
override every hardware choice the form offered. tests/genistub is a minimal
stand-in for geni.portal and geni.rspec.pg: enough to run the profile and
record the resulting request.

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


class PerRoleHardware(unittest.TestCase):
    """One experiment may request different hardware per node -- which is what
    makes a scarce type usable at all when only three machines of it exist."""

    def test_heterogeneous_preset_puts_the_scarce_type_only_on_storage(self):
        nodes, lans = request_for({"preset": "measurement-het"})
        storage = {k: v for k, v in nodes.items() if k.startswith("db")}
        other = {k: v for k, v in nodes.items() if not k.startswith("db")}
        self.assertEqual({v["hw"] for v in storage.values()}, {"r6615"})
        self.assertEqual({v["hw"] for v in other.values()}, {"c6420"})
        self.assertEqual(len(storage), 3)
        # and they all share ONE experiment LAN, which is the whole point:
        # separate experiments would have to talk over the control network
        self.assertEqual(len(lans), 1)
        self.assertIn("8 ifaces", lans[0])

    def test_homogeneous_preset_is_unchanged(self):
        nodes, _ = request_for({"preset": "measurement"})
        self.assertEqual({v["hw"] for v in nodes.values()}, {"c6525-25g"})

    def test_storage_type_can_be_overridden_alone(self):
        nodes, _ = request_for({"preset": "measurement-het",
                                "storage_hw_type": "c6525-25g"})
        self.assertEqual(nodes["db1"]["hw"], "c6525-25g")
        self.assertEqual(nodes["fe1"]["hw"], "c6420")

    def test_custom_storage_type_beats_the_field(self):
        nodes, _ = request_for({"preset": "measurement-het",
                                "storage_hw_type": "c6320",
                                "storage_hw_type_custom": "xl170"})
        self.assertEqual(nodes["db1"]["hw"], "xl170")

    def test_storage_falls_back_to_the_cluster_type(self):
        nodes, _ = request_for({"preset": "custom", "hw_type": "c6420",
                                "num_db_hosts": 3, "num_fe_hosts": 1})
        self.assertEqual({v["hw"] for v in nodes.values()}, {"c6420"})

    def test_only_storage_hosts_get_a_data_blockstore(self):
        nodes, _ = request_for({"preset": "measurement-het"})
        for name, v in nodes.items():
            if name.startswith("db"):
                self.assertIn("/mnt/data", v["store"], name)
            else:
                self.assertEqual(v["store"], "-", name)


class PresetPrecedence(unittest.TestCase):
    """The bug this harness exists to catch: a preset that silently discarded
    every hardware choice the form offered."""

    def test_an_explicit_pick_beats_the_preset(self):
        nodes, _ = request_for({"preset": "measurement", "hw_type": "d6515"})
        self.assertEqual(nodes["ctl1"]["hw"], "d6515")

    def test_custom_beats_both(self):
        nodes, _ = request_for({"preset": "measurement", "hw_type": "d6515",
                                "hw_type_custom": "m510"})
        self.assertEqual(nodes["ctl1"]["hw"], "m510")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SingleCluster(unittest.TestCase):
    """One LAN means one CloudLab cluster.

    An experiment can span aggregates, but the LAN between them is a stitched
    wide-area link: tens of milliseconds RTT against ~0.1 ms locally. Raft
    would pay that on every commit and the fsync-bound write capacity this
    project measures would be replaced by a network-bound one.
    """

    def test_types_from_one_cluster_are_accepted(self):
        nodes, _ = request_for({"preset": "measurement-het"})   # both Clemson
        self.assertEqual(nodes["db1"]["hw"], "r6615")
        self.assertEqual(nodes["fe1"]["hw"], "c6420")

    def test_types_split_across_clusters_are_refused(self):
        with self.assertRaises(AssertionError) as e:
            request_for({"preset": "measurement-het", "hw_type": "c6420",
                         "storage_hw_type": "c6525-25g"})
        self.assertIn("different CloudLab clusters", str(e.exception))

    def test_an_unknown_type_is_not_assumed_remote(self):
        # a new hardware type is new, not necessarily elsewhere
        nodes, _ = request_for({"preset": "measurement-het",
                                "storage_hw_type_custom": "brand-new-type"})
        self.assertEqual(nodes["db1"]["hw"], "brand-new-type")
