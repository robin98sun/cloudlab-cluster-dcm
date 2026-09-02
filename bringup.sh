#!/usr/bin/env bash
# Bring a freshly instantiated testbed to a fully deployed state.
#
# Idempotent: safe to re-run. Everything it does is derived from topology.json,
# so it works for any cluster shape. Node hostnames come from the CloudLab manifest.
#
#   ./bringup.sh                 # full bring-up
#   ./bringup.sh --skip-smoke    # skip the 13-check suite
set -uo pipefail
cd "$(dirname "$0")"
TOPO=${TOPOLOGY:-topology.json}
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SKIP_SMOKE=0
[ "${1:-}" = "--skip-smoke" ] && SKIP_SMOKE=1

host_of() { python3 -c "
import json,sys
t=json.load(open('$TOPO'))
print(next(n['control'] for n in t['nodes'] if n['role']=='$1'))"; }

[ -f "$TOPO" ] || { echo "no $TOPO -- run 'make topology' first"; exit 1; }
CTL=$(host_of ctl)
echo "== ctl node: $CTL"

echo "== 1/4 monitoring stack"
scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    monitoring/monitoring.yaml monitoring/dashboard.yaml "robin98@$CTL:/tmp/" \
    || { echo "   FAILED to copy manifests"; exit 1; }
$SSH "robin98@$CTL" "sudo /usr/local/bin/k3s kubectl apply -f /tmp/monitoring.yaml -f /tmp/dashboard.yaml" \
    | tail -2 | sed 's/^/   /' || { echo "   FAILED to apply"; exit 1; }

echo "== 2/4 cpuset agent (+ storage namespace)"
$SSH "robin98@$CTL" "kubectl create namespace storage --dry-run=client -o yaml | sudo /usr/local/bin/k3s kubectl apply -f - >/dev/null 2>&1" || true
scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    cpu/cpuset-agent.yaml "robin98@$CTL:/tmp/" \
    || { echo "   FAILED to copy manifest"; exit 1; }
$SSH "robin98@$CTL" "sudo /usr/local/bin/k3s kubectl apply -f /tmp/cpuset-agent.yaml" \
    | tail -1 | sed 's/^/   /' || { echo "   FAILED to apply"; exit 1; }

echo "== 3/4 waiting for pods"
for _ in $(seq 1 60); do
    bad=$($SSH "robin98@$CTL" "kubectl get pods -A --no-headers 2>/dev/null | grep -v ' Running ' | wc -l" 2>/dev/null | tr -dc '0-9')
    [ -n "$bad" ] && [ "$bad" -eq 0 ] && break
    sleep 5
done
$SSH "robin98@$CTL" "kubectl get pods -A --no-headers | awk '{print \$1, \$2, \$4}'" | sed 's/^/   /'

if [ "$SKIP_SMOKE" -eq 0 ]; then
    echo "== 4/4 smoke suite"
    python3 orchestrator/smoke.py --topology "$TOPO" --out smoke-results.json 2>&1 | tail -3
else
    echo "== 4/4 smoke skipped"
fi

echo
echo "Grafana:    http://$CTL:3000"
echo "Prometheus: http://$CTL:9090"
