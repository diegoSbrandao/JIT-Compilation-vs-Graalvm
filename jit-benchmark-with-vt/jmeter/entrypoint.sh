#!/bin/bash
set -e

JVM_HOST=${JVM_HOST:-app-jvm}
JVM_PORT=${JVM_PORT:-8080}
NATIVE_HOST=${NATIVE_HOST:-app-native}
NATIVE_PORT=${NATIVE_PORT:-8081}
PROM_HOST=${PROM_HOST:-prometheus}
PROM_PORT=${PROM_PORT:-9090}
THREADS=${THREADS:-20}
DURATION_SEC=${DURATION_SEC:-600}
RESULTS_DIR=${RESULTS_DIR:-/results}
SCENARIO=${SCENARIO:-}

mkdir -p "$RESULTS_DIR"
rm -f  "${RESULTS_DIR}/raw.csv" "${RESULTS_DIR}/report.html"
rm -f  "${RESULTS_DIR}/jmeter.log" "${RESULTS_DIR}/metrics_before.json"
rm -f  "${RESULTS_DIR}/metrics_after.json" "${RESULTS_DIR}/metrics_timeline.jsonl"

echo "════════════════════════════════════════════"
echo "  JIT vs Native Image Benchmark"
[ -n "$SCENARIO" ] && echo "  Cenário: $SCENARIO"
echo "  Duração: ${DURATION_SEC}s · Threads: ${THREADS}"
echo "  JVM:    http://${JVM_HOST}:${JVM_PORT}"
echo "  Native: http://${NATIVE_HOST}:${NATIVE_PORT}"
echo "════════════════════════════════════════════"

wait_for() {
    local name=$1 url=$2
    echo "Aguardando ${name}..."
    local tries=0
    until curl -sf "$url" | grep -q UP 2>/dev/null; do
        tries=$((tries+1))
        [ $tries -ge 90 ] && echo "ERRO: ${name} timeout" && exit 1
        [ $((tries % 10)) -eq 0 ] && echo "  ... ${tries}/90"
        sleep 2
    done
    echo "  ✓ ${name}"
}

collect_snapshot() {
    local label="$1"
    python3 - << PYEOF
import urllib.request, urllib.parse, json
from datetime import datetime, timezone

def pq(query):
    try:
        url = f"http://${PROM_HOST}:${PROM_PORT}/api/v1/query?query={urllib.parse.quote(query)}"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        results = data.get("data",{}).get("result",[])
        if results:
            return round(float(results[0].get("value",[None,None])[1]),2)
        return None
    except: return None

snap = {
    "ts":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "label":  "$label",
    "jvm": {
        "heap_mb":    pq("jvm_memory_used_bytes{application='app-jvm',area='heap'}/1024/1024"),
        "cpu_pct":    pq("process_cpu_usage{application='app-jvm'}*100"),
        "gc_pause_s": pq("sum(jvm_gc_pause_seconds_sum{application='app-jvm'})"),
        "threads":    pq("jvm_threads_live_threads{application='app-jvm'}"),
        "tps":        pq("sum(rate(http_server_requests_seconds_count{application='app-jvm',uri!~'/actuator.*'}[30s]))"),
        # Tempo real de boot até a app ficar pronta (gauge fixado 1x no startup pelo
        # StartupTimeMetricsListener do Spring Boot Actuator). Sem isso, NÃO inventamos
        # número de startup no relatório — ele aparece como "não medido".
        "ready_s":    pq("application_ready_time_seconds{application='app-jvm'}"),
    },
    "native": {
        "heap_mb":    pq("jvm_memory_used_bytes{application='app-native',area='heap'}/1024/1024"),
        "cpu_pct":    pq("process_cpu_usage{application='app-native'}*100"),
        "gc_pause_s": pq("sum(jvm_gc_pause_seconds_sum{application='app-native'})"),
        "threads":    pq("jvm_threads_live_threads{application='app-native'}"),
        "tps":        pq("sum(rate(http_server_requests_seconds_count{application='app-native',uri!~'/actuator.*'}[30s]))"),
        "ready_s":    pq("application_ready_time_seconds{application='app-native'}"),
    },
}
with open("${RESULTS_DIR}/metrics_timeline.jsonl", "a") as f:
    f.write(json.dumps(snap) + "\n")

jh=snap["jvm"]["heap_mb"]; nh=snap["native"]["heap_mb"]
jc=snap["jvm"]["cpu_pct"]; nc=snap["native"]["cpu_pct"]
jt=snap["jvm"]["tps"];     nt=snap["native"]["tps"]
print(f"  [{snap['label']:8s}] JVM  heap={jh}MB cpu={jc}% tps={jt}")
print(f"  [{snap['label']:8s}] Nat  heap={nh}MB cpu={nc}% tps={nt}")
PYEOF
}

wait_for "app-jvm"    "http://${JVM_HOST}:${JVM_PORT}/actuator/health"
wait_for "app-native" "http://${NATIVE_HOST}:${NATIVE_PORT}/actuator/health"

echo ""
echo "Aguardando Prometheus (primeiros scrapes)..."
TRIES=0
until python3 /jmeter/prom_query.py "$PROM_HOST" "$PROM_PORT" \
    "jvm_memory_used_bytes{application='app-jvm',area='heap'}" 2>/dev/null | grep -qv "n/a"; do
    TRIES=$((TRIES+1))
    [ $TRIES -ge 30 ] && break
    sleep 2
done
echo "  ✓ Prometheus pronto"

echo ""
echo "Snapshot inicial:"
collect_snapshot "before"
echo ""
echo "  Grafana:    http://localhost:3000 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo ""

( i=0; while true; do sleep 15; i=$((i+15)); collect_snapshot "t${i}s"; done ) &
COLLECTOR_PID=$!

echo "Executando JMeter (${DURATION_SEC}s)..."
jmeter -n \
    -t /jmeter/benchmark.jmx \
    -JJVM_HOST="${JVM_HOST}" -JJVM_PORT="${JVM_PORT}" \
    -JNATIVE_HOST="${NATIVE_HOST}" -JNATIVE_PORT="${NATIVE_PORT}" \
    -JTHREADS="${THREADS}" -JDURATION_SEC="${DURATION_SEC}" \
    2>&1 | tee "${RESULTS_DIR}/jmeter.log"

kill $COLLECTOR_PID 2>/dev/null || true

echo ""
echo "Snapshot final:"
collect_snapshot "after"

python3 - << 'PYEOF'
import json, os
d = os.environ.get("RESULTS_DIR", "/results")
try:
    lines = [json.loads(l) for l in open(f"{d}/metrics_timeline.jsonl") if l.strip()]
    if lines:
        json.dump(lines[0],  open(f"{d}/metrics_before.json","w"))
        json.dump(lines[-1], open(f"{d}/metrics_after.json","w"))
        print(f"  {len(lines)} snapshots salvos")
except Exception as e:
    print(f"  aviso: {e}")
PYEOF

echo ""
echo "--- Diagnóstico ---"
if [ -f "${RESULTS_DIR}/raw.csv" ]; then
    echo "  Samples: $(($(wc -l < "${RESULTS_DIR}/raw.csv")-1))"
    echo "  Labels:"
    awk -F',' 'NR>1{print $3}' "${RESULTS_DIR}/raw.csv" | sort -u | sed 's/^/    /'
else
    echo "  AVISO: raw.csv não gerado!"
fi

echo ""
echo "Gerando relatório HTML..."
RESULTS_DIR="$RESULTS_DIR" SCENARIO="$SCENARIO" DURATION_SEC="$DURATION_SEC" python3 /jmeter/report.py

echo ""
echo "════════════════════════════════════════════"
echo "  Resultados em ${RESULTS_DIR}:"
echo "    report.html             → relatório"
echo "    raw.csv                 → todos os samples"
echo "    metrics_timeline.jsonl  → métricas Prometheus"
echo "  Grafana: http://localhost:3000"
echo "════════════════════════════════════════════"
