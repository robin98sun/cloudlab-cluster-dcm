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
        self.assertEqual({v["hw"] for v in storage.values()}, {"r6615"})
        self.assertEqual(len(storage), 3)
        # and nothing else takes the scarce type
        for k, v in nodes.items():
            if not k.startswith("db"):
                self.assertNotEqual(v["hw"], "r6615", k)
        # and they all share ONE experiment LAN, which is the whole point:
        # separate experiments would have to talk over the control network
        self.assertEqual(len(lans), 1)
        self.assertIn("8 ifaces", lans[0])

    def test_homogeneous_preset_is_unchanged(self):
        nodes, _ = request_for({"preset": "measurement"})
        self.assertEqual({v["hw"] for v in nodes.values()}, {"c6525-25g"})

    def test_storage_type_can_be_overridden_alone(self):
        # Same cluster: c6320 and c6420 are both at Clemson. (An earlier
        # version of this test used a Utah type here and now fails, correctly,
        # against the single-cluster guard below.)
        nodes, _ = request_for({"preset": "measurement-het",
                                "storage_hw_type": "c6320"})
        self.assertEqual(nodes["db1"]["hw"], "c6320")
        # the load group keeps the preset's own choice
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_custom_storage_type_beats_the_field(self):
        # xl170 is not in HW_CLUSTER, so the guard leaves it alone
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




class SingleCluster(unittest.TestCase):
    """One LAN means one CloudLab cluster.

    An experiment can span aggregates, but the LAN between them is a stitched
    wide-area link: tens of milliseconds RTT against ~0.1 ms locally. Raft
    would pay that on every commit and the fsync-bound write capacity this
    project measures would be replaced by a network-bound one.
    """

    def test_types_from_one_cluster_are_accepted(self):
        # r6615, r7525 and c6420 are all Clemson
        nodes, _ = request_for({"preset": "measurement-het"})
        self.assertEqual(nodes["db1"]["hw"], "r6615")
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_types_split_across_clusters_are_refused(self):
        with self.assertRaises(AssertionError) as e:
            request_for({"preset": "measurement-het", "hw_type": "c6420",
                         "storage_hw_type": "c6525-25g"})
        self.assertIn("Pick every type from one cluster", str(e.exception))

    def test_an_unknown_type_is_not_assumed_remote(self):
        # a new hardware type is new, not necessarily elsewhere
        nodes, _ = request_for({"preset": "measurement-het",
                                "storage_hw_type_custom": "brand-new-type"})
        self.assertEqual(nodes["db1"]["hw"], "brand-new-type")


class PerGroupHardware(unittest.TestCase):
    """Three groups, three types, one experiment (user, 2026-08-27)."""

    def test_each_group_takes_its_own_type(self):
        nodes, lans = request_for({"preset": "measurement-het"})
        self.assertEqual({v["hw"] for k, v in nodes.items()
                          if k.startswith("db")}, {"r6615"})
        self.assertEqual({v["hw"] for k, v in nodes.items()
                          if k.startswith(("lg", "fe"))}, {"r7525"})
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")
        self.assertEqual(len(lans), 1)      # still ONE experiment LAN

    def test_load_type_can_be_set_alone(self):
        nodes, _ = request_for({"preset": "custom", "hw_type": "c6420",
                                "load_hw_type": "r7525",
                                "num_db_hosts": 3, "num_fe_hosts": 1,
                                "num_lg_hosts": 1})
        self.assertEqual(nodes["db1"]["hw"], "c6420")
        self.assertEqual(nodes["lg1"]["hw"], "r7525")
        self.assertEqual(nodes["fe1"]["hw"], "r7525")
        self.assertEqual(nodes["ctl1"]["hw"], "c6420")

    def test_control_type_can_be_set_alone(self):
        nodes, _ = request_for({"preset": "custom", "hw_type": "c6420",
                                "ctl_hw_type": "c6320",
                                "num_db_hosts": 3, "num_fe_hosts": 1})
        self.assertEqual(nodes["ctl1"]["hw"], "c6320")
        self.assertEqual(nodes["db1"]["hw"], "c6420")

    def test_any_group_crossing_clusters_is_refused(self):
        # the guard now covers every group, not just storage
        with self.assertRaises(AssertionError) as e:
            request_for({"preset": "custom", "hw_type": "c6420",
                         "load_hw_type": "c6525-25g",
                         "num_db_hosts": 3, "num_fe_hosts": 1})
        self.assertIn("Pick every type from one cluster", str(e.exception))


class HardwareFacts(unittest.TestCase):
    """Core count is not speed. From CloudLab's own hardware page."""

    def _profile(self):
        return open(os.path.join(ROOT, "profile.py")).read()

    def test_clock_speeds_are_recorded(self):
        src = self._profile()
        self.assertIn("HW_GHZ", src)
        for t, ghz in (("c6320", "2.0"), ("c6420", "2.6"),
                       ("r7525", "2.9"), ("r6615", "3.25")):
            self.assertIn('"%s": %s' % (t, ghz), src, t)

    def test_labels_state_cpu_and_era_not_only_cores(self):
        src = self._profile()
        # the trap: c6320 has 28 cores of 2014 Haswell at 2.0GHz
        self.assertIn("E5-2683v3", src)
        self.assertIn("oldest cores", src)
        self.assertIn("9354P", src)


class Availability(unittest.TestCase):
    """What maps is a harder constraint than what is fast.

    Observed 2026-08-27 at Clemson: c6320 16 free, everything else 0-3, and
    EVERY free type is HDD-only. A preset naming r6615 and r7525 is
    aspirational there, not runnable.
    """

    def test_the_available_preset_maps_to_one_plentiful_type(self):
        nodes, lans = request_for({"preset": "clemson-available"})
        self.assertEqual({v["hw"] for v in nodes.values()}, {"c6320"})
        self.assertEqual(len(lans), 1)

    def test_flash_requirement_refuses_a_spindle_storage_type(self):
        with self.assertRaises(AssertionError) as e:
            request_for({"preset": "clemson-available",
                         "require_flash_storage": True})
        # request_for keeps only the last 400 chars of stderr, so assert on
        # wording that survives the trim
        self.assertIn("every currently-free type is HDD-only", str(e.exception))

    def test_flash_requirement_accepts_a_flash_storage_type(self):
        nodes, _ = request_for({"preset": "measurement",
                                "require_flash_storage": True})
        self.assertEqual(nodes["db1"]["hw"], "c6525-25g")   # SATA SSD

    def test_disk_class_is_recorded_for_every_listed_type(self):
        src = open(os.path.join(ROOT, "profile.py")).read()
        self.assertIn("HW_DISK", src)
        # the free-at-Clemson types are all spindles, and saying so is the
        # point of the table
        for t in ("c6320", "c8220", "c8220x", "c4130", "ibm8335"):
            self.assertIn('"%s": "hdd"' % t, src, t)
        for t in ("r6615", "c6620", "d7615", "c6525-100g", "r650"):
            self.assertIn('"%s": "nvme"' % t, src, t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
