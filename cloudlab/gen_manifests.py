"""Generate the k8s objects for the testbed as one List manifest.

Written as JSON (a YAML subset) so generation needs no dependencies and the
output is trivially machine-checkable. Placement is deliberately explicit:
every pod is pinned to a hostname. The experiment wants placement to be a
recorded decision, not a scheduler outcome.

Pods run hostNetwork so the measured path uses the real client/backend
interfaces -- flannel exists only for cluster plumbing on the control net.
FE pods on one host take distinct ports 8081, 8082, ...
"""

import argparse
import json

PY_IMAGE = "docker.io/library/python:3.11-slim"
LETTERS = "abcdefghij"

# CPU plan (see cpu/cpuset-agent.yaml). Pods declare physical-core COUNTS;
# the agent maps them onto whatever topology the node actually has, so
# nothing here assumes a core count, an SMT width, or a pool size. System
# reservation and shared-pool size are node policy (ConfigMap cpuset-policy).
# Requests are set for scheduling; limits are deliberately NEVER set, because
# a CPU limit installs a CFS quota whose 100ms periods inject latency
# artifacts into knee detection.
FE_CORES = 1
DB_CORES = 2
LG_CORES = 4


def cpu_annotations(cores, weight="5000"):
    return {
        "testbed/dedicated-cores": str(cores),
        "testbed/cpu-weight": weight,
    }


def pod(name, node, kind, command, volumes, mounts, annotations=None,
        cpu_request=None):
    meta = {
        "name": name,
        "namespace": "testbed",
        "labels": {"app": "testbed", "testbed/kind": kind},
    }
    if annotations:
        meta["annotations"] = annotations
    container = {
        "name": kind,
        "image": PY_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": command,
        "volumeMounts": mounts,
    }
    if cpu_request:
        # requests only -- never limits (see CPU plan note above)
        container["resources"] = {"requests": {"cpu": cpu_request,
                                               "memory": "1Gi"}}
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": meta,
        "spec": {
            "nodeSelector": {"kubernetes.io/hostname": node},
            "hostNetwork": True,
            "dnsPolicy": "Default",
            "restartPolicy": "Always",
            "containers": [container],
            "volumes": volumes,
        },
    }


def host_path(name, path, create=False):
    v = {"name": name, "hostPath": {"path": path}}
    if create:
        v["hostPath"]["type"] = "DirectoryOrCreate"
    return v


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fe-hosts", type=int, default=1)
    p.add_argument("--db-hosts", type=int, default=1)
    p.add_argument("--fe-instances", type=int, default=3)
    p.add_argument("--fe-base-port", type=int, default=8081)
    p.add_argument("--db-port", type=int, default=9091)
    p.add_argument("--fe-cores", type=int, default=FE_CORES,
                   help="dedicated physical cores per frontend pod")
    p.add_argument("--db-cores", type=int, default=DB_CORES,
                   help="dedicated physical cores per storage pod")
    p.add_argument("--lg-cores", type=int, default=LG_CORES,
                   help="dedicated physical cores for the load generator")
    p.add_argument("--no-db", action="store_true",
                   help="skip stub storage pods (a real storage tier is deployed)")
    p.add_argument("--lg-host", default="ctl1",
                   help="host for the load-generator pod")
    p.add_argument("--out", default="testbed.yaml")
    a = p.parse_args()

    common_volumes = [
        host_path("repo", "/local/repository"),
        host_path("testbed", "/local/testbed", create=True),
    ]
    common_mounts = [
        {"name": "repo", "mountPath": "/repo", "readOnly": True},
        {"name": "testbed", "mountPath": "/local/testbed"},
    ]

    items = [{
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "testbed"},
    }]

    for j in range(1, a.fe_hosts + 1):
        for i in range(a.fe_instances):
            name = "fe%d-%s" % (j, LETTERS[i])
            port = a.fe_base_port + i
            items.append(pod(
                name, "fe%d" % j, "fe",
                ["python3", "/repo/services/stub_fe.py",
                 "--port", str(port),
                 "--instance-id", name,
                 "--db-port", str(a.db_port),
                 "--destinations-file", "/local/testbed/destinations.json",
                 "--telemetry-dir", "/local/testbed/telemetry"],
                common_volumes, common_mounts,
                annotations=cpu_annotations(a.fe_cores),
                cpu_request=str(a.fe_cores)))

    for k in range(1, 0 if a.no_db else a.db_hosts + 1):
        items.append(pod(
            "db%d" % k, "db%d" % k, "db",
            ["python3", "/repo/services/stub_db.py",
             "--port", str(a.db_port),
             "--data-dir", "/data/store"],
            common_volumes + [host_path("data", "/mnt/data", create=True)],
            common_mounts + [{"name": "data", "mountPath": "/data"}],
            annotations=cpu_annotations(a.db_cores),
            cpu_request=str(a.db_cores)))

    # Load generator pod on the ctl host (or a dedicated lg host when the
    # measurement preset allocates one). Idle until the harness drives it.
    items.append(pod(
        "lg1", a.lg_host, "lg",
        ["sleep", "infinity"],
        common_volumes, common_mounts,
        annotations=cpu_annotations(a.lg_cores, weight="10000"),
        cpu_request=str(a.lg_cores)))

    doc = {"apiVersion": "v1", "kind": "List", "items": items}
    with open(a.out, "w") as fh:
        json.dump(doc, fh, indent=2)
    print("wrote %s: %d objects (%d fe pods, %d db pods)"
          % (a.out, len(items), a.fe_hosts * a.fe_instances, a.db_hosts))


if __name__ == "__main__":
    main()
