#!/usr/bin/env python3
"""
Relatório JIT vs Native Image — detecta labels automaticamente do raw.csv.
Funciona com [JVM] / [NATIVE] independente do cenário (with-vt ou no-vt).
"""
import csv, sys, os, json
from datetime import datetime
from collections import defaultdict

RESULTS_DIR = os.environ.get("RESULTS_DIR", "/results")
RAW_CSV     = os.path.join(RESULTS_DIR, "raw.csv")
MEM_BEFORE  = os.path.join(RESULTS_DIR, "metrics_before.json")
MEM_AFTER   = os.path.join(RESULTS_DIR, "metrics_after.json")
METRICS_TL  = os.path.join(RESULTS_DIR, "metrics_timeline.jsonl")
OUT_HTML    = os.path.join(RESULTS_DIR, "report.html")
SCENARIO    = os.environ.get("SCENARIO", "")

# Limites de container — vêm das env vars setadas no docker-compose.yml (serviço
# jmeter), que devem ser mantidas iguais a mem_limit/cpus dos serviços app-jvm e
# app-native. Nunca hardcode esse texto direto no HTML: se a env var não vier,
# mostramos "?" em vez de inventar um valor que pode estar desatualizado.
JVM_MEM_MB    = os.environ.get("JVM_MEM_MB", "?")
JVM_CPUS      = os.environ.get("JVM_CPUS", "?")
NATIVE_MEM_MB = os.environ.get("NATIVE_MEM_MB", "?")
NATIVE_CPUS   = os.environ.get("NATIVE_CPUS", "?")
# Duração esperada do teste (a mesma passada ao JMeter via -JDURATION_SEC).
# Usada só para detectar rodadas interrompidas e avisar — não afeta os cálculos.
try: EXPECTED_DURATION_SEC = float(os.environ.get("DURATION_SEC", "0"))
except ValueError: EXPECTED_DURATION_SEC = 0

def load_csv(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def load_json(p):
    try:
        with open(p) as f: return json.load(f)
    except: return {}

def load_jsonl(p):
    rows = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try: rows.append(json.loads(line))
                    except: pass
    except: pass
    return rows

def pct(vals, p):
    if not vals: return 0
    s = sorted(vals); k=(len(s)-1)*p/100; f,c=int(k),min(int(k)+1,len(s)-1)
    return round(s[f]+(s[c]-s[f])*(k-f),1)

def stats(rows, label):
    lats,errs=[],0
    for r in rows:
        if r.get("label","").strip()!=label: continue
        try: lats.append(int(r["elapsed"]))
        except: continue
        if r.get("success","true").lower()!="true": errs+=1
    if not lats: return None
    total=len(lats)
    ts=[int(r["timeStamp"])/1000 for r in rows
        if r.get("label","").strip()==label and r.get("timeStamp","0").isdigit()]
    tps=round(len(ts)/(max(ts)-min(ts)),1) if len(ts)>2 else 0
    return dict(count=total,errors=errs,error_pct=round(errs/total*100,2),
                avg=round(sum(lats)/total,1),min=min(lats),max=max(lats),
                p50=pct(lats,50),p90=pct(lats,90),p95=pct(lats,95),p99=pct(lats,99),tps=tps)

def warmup_curve(rows, label, bucket=5):
    pts=defaultdict(list)
    for r in rows:
        if r.get("label","").strip()!=label: continue
        try:
            ts=int(r["timeStamp"])/1000; e=int(r["elapsed"])
            pts[int(ts//bucket)*bucket].append(e)
        except: continue
    if not pts: return []
    base=min(pts)
    return [(k-base,round(sum(v)/len(v),1),len(v)) for k,v in sorted(pts.items())]

def sparkline(pts, color, width=280, height=52):
    if not pts: return ""
    ys=[p[1] for p in pts]; xs=[p[0] for p in pts]
    mn,mx=min(ys),max(ys)
    if mx==mn: mx=mn+1
    xmx=max(xs) if max(xs)>0 else 1
    def px(x,y):
        px_=int(x/xmx*(width-4))+2
        py_=height-2-int((y-mn)/(mx-mn)*(height-4))
        return f"{px_},{py_}"
    pts_str=" ".join(px(x,y) for x,y in zip(xs,ys))
    return (f'<svg width="{width}" height="{height}" style="display:block;margin:4px 0">'
            f'<polyline points="{pts_str}" fill="none" stroke="{color}" stroke-width="1.8"/>'
            f'<text x="2" y="11" font-size="9" fill="#9E9E9E">{mx:.1f}ms</text>'
            f'<text x="2" y="{height-1}" font-size="9" fill="#9E9E9E">{mn:.1f}ms</text>'
            f'</svg>')

def snap_or_tl(before, after, tl, runtime, key, use_last=False):
    def get(d):
        try:
            v=d.get(runtime,{}).get(key)
            if v is None: return "—"
            f=float(str(v))
            return "—" if f<0 else round(f,1)
        except: return "—"
    v=get(after if use_last else before)
    if v!="—": return v
    pts=[s for s in tl if get(s)!="—"]
    if not pts: return "—"
    return get(pts[-1] if use_last else pts[0])

# ── detecta prefixos reais do CSV ──────────────────────────────────────────
def detect_labels(rows):
    """Detecta automaticamente [JVM] e [NATIVE] no CSV."""
    all_labels = set(r.get("label","").strip() for r in rows)
    jvm_prefix=nat_prefix=None
    for l in all_labels:
        if "[JVM]" in l and jvm_prefix is None: jvm_prefix="[JVM]"
        if "[NATIVE]" in l and nat_prefix is None: nat_prefix="[NATIVE]"
    return jvm_prefix or "[JVM]", nat_prefix or "[NATIVE]"

ENDPOINTS_DEF = [
    ("/api/ping",         "Ping",          "Latência base — sem cálculo. Overhead puro do runtime."),
    ("/api/primes/500",   "Primes/500",    "CPU-bound. Loop iterativo — hot path do compilador C2. Aqui o JIT mostra vantagem após o warmup."),
    ("/api/memory/1000",  "Memory/1000",   "Heap pressure. 1.000 alocações por request. Diferença entre G1GC (JVM) e Serial GC (Native)."),
    ("/api/fibonacci/38", "Fibonacci/38",  "Recursão pura. O C2 aplica method inlining agressivo eliminando overhead de chamada. Sem JIT, cada chamada é individual."),
    ("/api/serialize/100","Serialize/100", "Criação de objetos + reflexão. O JIT aprende o padrão e usa escape analysis — objetos que não escapam o método podem ir para a stack."),
    ("/api/sort/10000",   "Sort/10000",    "Sorting. O C2 detecta o loop de comparação como hot path e aplica vetorização SIMD."),
    ("/api/concurrent/50","Concurrent/50", "50 tarefas I/O em paralelo por request. O endpoint mais revelador sobre concorrência e throughput sob carga I/O-bound."),
]

CSS = """
* { box-sizing:border-box;margin:0;padding:0; }
body { font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;color:#212121; }
header { background:#0A2342;color:white;padding:24px 40px; }
header h1 { font-size:1.45rem; }
header p  { color:#90CAF9;font-size:.85rem;margin-top:4px; }
nav { background:white;border-bottom:1px solid #E0E0E0;padding:0 40px;
      display:flex;gap:0;overflow-x:auto; }
nav a { display:inline-block;padding:12px 16px;font-size:.82rem;color:#555;
        text-decoration:none;border-bottom:3px solid transparent;white-space:nowrap; }
nav a:hover { color:#1E88E5;border-bottom-color:#1E88E5; }
main { max-width:1080px;margin:28px auto;padding:0 20px 60px; }
.cards { display:flex;gap:14px;margin-bottom:28px;flex-wrap:wrap; }
.card { background:white;border-radius:8px;padding:18px 20px;
        box-shadow:0 2px 8px rgba(0,0,0,.07);flex:1;min-width:140px; }
.card h4 { font-size:.72rem;color:#9E9E9E;text-transform:uppercase;
           letter-spacing:.5px;margin-bottom:6px; }
.card .val { font-size:1.45rem;font-weight:bold;color:#0A2342;line-height:1.2; }
.card .sub { font-size:.75rem;color:#BDBDBD;margin-top:4px; }
.card.hl  { border-left:4px solid #43A047; }
.section  { background:white;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.07);
            padding:22px;margin-bottom:22px; }
.section h2 { font-size:1.05rem;color:#0A2342;margin-bottom:3px; }
.section-sub { font-size:.8rem;color:#9E9E9E;margin-bottom:14px; }
table { width:100%;border-collapse:collapse;font-size:.88rem; }
thead { background:#0A2342;color:white; }
th { padding:9px 12px;text-align:left;font-weight:600; }
td { padding:8px 12px;border-bottom:1px solid #F5F5F5; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:#FAFAFA; }
td:first-child { color:#616161;font-size:.82rem; }
.good { color:#1B5E20;font-weight:bold;background:#F1F8E9; }
.bad  { color:#B71C1C;background:#FFF8F8; }
.ww   { display:flex;gap:20px;flex-wrap:wrap;margin-top:10px; }
.wc   { flex:1;min-width:200px; }
.wc h4 { font-size:.8rem;color:#616161;margin-bottom:4px; }
.note { background:#E3F2FD;border-left:4px solid #1E88E5;
        padding:16px 20px;border-radius:8px;margin-bottom:22px; }
.note h3 { color:#0D47A1;margin-bottom:10px;font-size:.95rem; }
.note ul { padding-left:18px;line-height:2;font-size:.87rem; }
.warn-box { background:#FFF8E1;border-left:3px solid #FFB300;
            padding:10px 14px;border-radius:4px;font-size:.82rem;
            line-height:1.7;color:#5D4037;margin-top:12px; }
code { font-family:monospace;font-size:.8rem; }
footer { text-align:center;color:#BDBDBD;font-size:.78rem;padding:16px; }
"""

def build_html(rows, mem_b, mem_a, tl):
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    jvm_pfx, nat_pfx = detect_labels(rows)
    found = sorted(set(r.get("label","").strip() for r in rows))
    print(f"[report.py] {len(rows)} samples")
    print(f"[report.py] JVM prefix: '{jvm_pfx}' | Native prefix: '{nat_pfx}'")
    print(f"[report.py] Labels: {found[:6]}...")

    scen_txt = ""
    if "with-vt" in SCENARIO: scen_txt = " · Virtual Threads: HABILITADAS"
    elif "no-vt"  in SCENARIO: scen_txt = " · Virtual Threads: DESABILITADAS"

    # Contagens
    total_jvm = sum(1 for r in rows if r.get("label","").strip().startswith(jvm_pfx))
    total_nat = sum(1 for r in rows if r.get("label","").strip().startswith(nat_pfx))

    # ── Detecção de rodada incompleta ──────────────────────────────────────
    # Se a duração real medida (último timestamp - primeiro) ficar muito abaixo
    # do DURATION_SEC esperado, a rodada provavelmente foi interrompida — avisamos
    # em vez de apresentar como se fosse um teste completo igual aos outros.
    incomplete_banner = ""
    all_ts = [int(r["timeStamp"])/1000 for r in rows if r.get("timeStamp","0").isdigit()]
    if all_ts and EXPECTED_DURATION_SEC > 0:
        real_duration = max(all_ts) - min(all_ts)
        if real_duration < 0.9 * EXPECTED_DURATION_SEC:
            incomplete_banner = f"""
  <div class="warn-box" style="background:#FFEBEE;border-left:3px solid #C62828;color:#B71C1C;font-weight:bold;margin-bottom:18px">
    ⚠️ RODADA INCOMPLETA: durou {real_duration:.0f}s de {EXPECTED_DURATION_SEC:.0f}s esperados
    ({real_duration/EXPECTED_DURATION_SEC*100:.0f}%). O teste parece ter sido interrompido antes
    do fim — trate estes números como preliminares, não como comparação final.
  </div>"""

    # Throughput geral
    def total_tps(pfx):
        ts=[int(r["timeStamp"])/1000 for r in rows
            if r.get("label","").strip().startswith(pfx) and r.get("timeStamp","0").isdigit()]
        return round(len(ts)/(max(ts)-min(ts)),1) if len(ts)>2 else 0
    jvm_tps = total_tps(jvm_pfx)
    nat_tps = total_tps(nat_pfx)

    # Memória
    jvm_h_b = snap_or_tl(mem_b,mem_a,tl,"jvm","heap_mb",False)
    jvm_h_a = snap_or_tl(mem_b,mem_a,tl,"jvm","heap_mb",True)
    nat_h_b = snap_or_tl(mem_b,mem_a,tl,"native","heap_mb",False)
    nat_h_a = snap_or_tl(mem_b,mem_a,tl,"native","heap_mb",True)

    # ── Startup ──────────────────────────────────────────────────────────
    # NUNCA mostrar um número fixo aqui. Lemos application_ready_time_seconds
    # (gauge exposto automaticamente pelo Spring Boot Actuator) do snapshot
    # "before". Se a métrica não foi coletada nesta execução (ex: rodadas
    # antigas, antes deste fix), mostramos "não medido" — não inventamos valor.
    jvm_ready = snap_or_tl(mem_b,mem_a,tl,"jvm","ready_s",False)
    nat_ready = snap_or_tl(mem_b,mem_a,tl,"native","ready_s",False)
    try:
        _jr, _nr = float(jvm_ready), float(nat_ready)
        if _jr > 0 and _nr > 0:
            startup_val = f"~{round(_jr/_nr,1)}×"
            startup_sub = f"Native mais rápido ({_nr:.2f}s vs {_jr:.2f}s) · application_ready_time_seconds"
        else:
            raise ValueError
    except (ValueError, TypeError):
        startup_val = "não medido"
        startup_sub = "application_ready_time_seconds não coletado nesta execução"

    # Warmup curve — primes/500
    jvm_wc = warmup_curve(rows, f"{jvm_pfx} GET /api/primes/500")
    nat_wc = warmup_curve(rows, f"{nat_pfx} GET /api/primes/500")
    svg_jvm = sparkline(jvm_wc, "#1E88E5")
    svg_nat = sparkline(nat_wc, "#43A047")
    jvm_first = jvm_wc[0][1]  if jvm_wc else "—"
    jvm_last  = jvm_wc[-1][1] if jvm_wc else "—"
    nat_first = nat_wc[0][1]  if nat_wc else "—"
    nat_last  = nat_wc[-1][1] if nat_wc else "—"

    # CPU sparkline
    def cpu_svg(tl, key, color, width=280, height=40):
        vals=[]
        for s in tl:
            try:
                v=float(str(s.get(key,{}).get("cpu_pct","") or ""))
                if v>=0: vals.append(v)
            except: pass
        if len(vals)<2: return "<em style='font-size:.75rem;color:#9E9E9E'>sem dados</em>"
        mn,mx=min(vals),max(vals)
        if mx==mn: mx=mn+1
        w2=width-4
        pts=" ".join(f"{int(i/(len(vals)-1)*w2)+2},{height-2-int((v-mn)/(mx-mn)*(height-4))}"
                     for i,v in enumerate(vals))
        avg_v=round(sum(vals)/len(vals),1)
        return (f'<svg width="{width}" height="{height}" style="display:block">'
                f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>'
                f'<text x="2" y="10" font-size="8" fill="#9E9E9E">{mx:.0f}%</text>'
                f'<text x="2" y="{height-1}" font-size="8" fill="#9E9E9E">{mn:.0f}%</text>'
                f'</svg><div style="font-size:.75rem;color:#616161">média: <b>{avg_v}%</b></div>')

    jvm_cpu_svg = cpu_svg(tl,"jvm","#1E88E5")
    nat_cpu_svg = cpu_svg(tl,"native","#43A047")

    # Heap sparkline
    def heap_svg(tl, key, color, width=280, height=40):
        vals=[]
        for s in tl:
            try:
                v=float(str(s.get(key,{}).get("heap_mb","") or ""))
                vals.append(v)
            except: pass
        if len(vals)<2: return "<em style='font-size:.75rem;color:#9E9E9E'>sem dados</em>"
        mn,mx=min(vals),max(vals)
        if mx==mn: mx=mn+1
        w2=width-4
        pts=" ".join(f"{int(i/(len(vals)-1)*w2)+2},{height-2-int((v-mn)/(mx-mn)*(height-4))}"
                     for i,v in enumerate(vals))
        return (f'<svg width="{width}" height="{height}" style="display:block">'
                f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>'
                f'<text x="2" y="10" font-size="8" fill="#9E9E9E">{mx:.0f}MB</text>'
                f'<text x="2" y="{height-1}" font-size="8" fill="#9E9E9E">{mn:.0f}MB</text>'
                f'</svg><div style="font-size:.75rem;color:#616161">'
                f'início: <b>{vals[0]:.0f}MB</b> → final: <b>{vals[-1]:.0f}MB</b></div>')

    jvm_heap_svg = heap_svg(tl,"jvm","#1E88E5")
    nat_heap_svg = heap_svg(tl,"native","#43A047")

    # Endpoint stats — calculados UMA vez e reaproveitados na tabela e na
    # conclusão dinâmica abaixo, pra tabela e texto nunca poderem se contradizer.
    ep_stats = {}
    for ep_path, ep_name, ep_desc in ENDPOINTS_DEF:
        jl = f"{jvm_pfx} GET {ep_path}"
        nl = f"{nat_pfx} GET {ep_path}"
        ep_stats[ep_path] = (stats(rows, jl), stats(rows, nl), jl, nl)

    # Nota fixa só para o endpoint /api/concurrent/{n}: o controller sempre cria
    # Executors.newVirtualThreadPerTaskExecutor() internamente, independente do
    # cenário (no-vt/with-vt). A flag spring.threads.virtual.enabled só afeta as
    # threads do Tomcat que recebem a requisição, não essa chamada interna.
    CONCURRENT_VT_NOTE = (
        '<div class="warn-box" style="margin-top:10px">'
        '<b>Nota:</b> este endpoint usa <code>Executors.newVirtualThreadPerTaskExecutor()</code> '
        'internamente em ambos os apps, independente do cenário (com ou sem Virtual Threads '
        'habilitadas no Tomcat). Isso não afeta a comparação JIT vs Native — é simétrico nos '
        'dois lados — mas o resultado deste endpoint específico não reflete o efeito de '
        '"Virtual Threads habilitadas/desabilitadas" anunciado no cabeçalho.</div>'
    )

    # Endpoint sections
    def ep_section(ep_path, ep_name, ep_desc):
        js, ns, jl, nl = ep_stats[ep_path]
        jtp= js["tps"] if js else 0
        ntp= ns["tps"] if ns else 0
        if not js and not ns:
            ep_id=ep_path.replace("/api/","").replace("/","_")
            return f'<div class="section" id="{ep_id}"><h2>{ep_name}</h2><p style="color:#E65100">Sem dados — labels esperados: <code>{jl}</code> e <code>{nl}</code>. Labels no CSV: {found[:4]}</p></div>'

        def sv(s,k): return s[k] if s else "—"
        def mrow(name,jv,nv,lb=True,colorize=True):
            cj=cn=""
            if colorize:
                try:
                    fj,fn=float(str(jv)),float(str(nv))
                    if lb: cj="good" if fj<=fn else "bad"; cn="good" if fn<=fj else "bad"
                    else:  cj="good" if fj>=fn else "bad"; cn="good" if fn>=fj else "bad"
                except: pass
            return f"<tr><td>{name}</td><td class='{cj}'>{jv}</td><td class='{cn}'>{nv}</td></tr>"

        rows_html=(
            mrow("Amostras",       sv(js,"count"),     sv(ns,"count"),     lb=False)+
            mrow("Erros (%)",      sv(js,"error_pct"), sv(ns,"error_pct"))+
            mrow("Avg (ms)",       sv(js,"avg"),        sv(ns,"avg"))+
            mrow("Min (ms)",       sv(js,"min"),        sv(ns,"min"))+
            mrow("p50 (ms)",       sv(js,"p50"),        sv(ns,"p50"))+
            mrow("p90 (ms)",       sv(js,"p90"),        sv(ns,"p90"))+
            mrow("p95 (ms)",       sv(js,"p95"),        sv(ns,"p95"))+
            mrow("p99 (ms)",       sv(js,"p99"),        sv(ns,"p99"))+
            mrow("Max (ms)",       sv(js,"max"),        sv(ns,"max"))+
            mrow("Throughput r/s", jtp,                 ntp,                lb=False)
        )
        ep_id=ep_path.replace("/api/","").replace("/","_")
        extra_note = CONCURRENT_VT_NOTE if ep_path == "/api/concurrent/50" else ""
        return f"""
        <div class="section" id="{ep_id}">
          <h2>{ep_name} <span style="font-weight:normal;font-size:.83rem;color:#9E9E9E">— {ep_desc}</span></h2>
          <table>
            <thead><tr>
              <th>Métrica</th>
              <th>🔵 HotSpot JIT</th>
              <th>🟢 GraalVM Native</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
          {extra_note}
        </div>"""

    ep_sections="".join(ep_section(*e) for e in ENDPOINTS_DEF)
    nav="".join(f'<a href="#{e[0].replace("/api/","").replace("/","_")}">{e[1]}</a>' for e in ENDPOINTS_DEF)

    # ── Conclusão dinâmica ──────────────────────────────────────────────────
    # Em vez de texto fixo dizendo quem "vence" em CPU-bound/memória (que pode
    # contradizer a própria tabela dependendo da rodada), comparamos avg e p99
    # reais de cada endpoint e contamos quem ganhou. Critério: precisa vencer em
    # avg E p99 para contar como vitória clara; senão entra como "indefinido".
    def decide(js, ns):
        if not js or not ns: return None
        if js["avg"] < ns["avg"] and js["p99"] <= ns["p99"]: return "jvm"
        if ns["avg"] < js["avg"] and ns["p99"] <= js["p99"]: return "native"
        return "indef"

    jvm_wins, nat_wins, indef = [], [], []
    for ep_path, ep_name, _ in ENDPOINTS_DEF:
        js, ns, _, _ = ep_stats[ep_path]
        d = decide(js, ns)
        if d == "jvm": jvm_wins.append(ep_name)
        elif d == "native": nat_wins.append(ep_name)
        elif d == "indef": indef.append(ep_name)

    def fmt(lst): return ", ".join(lst) if lst else "nenhum endpoint"
    conclusion_bullet = (
        f"<li><b>Resultado real desta execução</b> (vitória = avg E p99 melhores ao mesmo tempo): "
        f"HotSpot JIT venceu em <b>{fmt(jvm_wins)}</b>; GraalVM Native venceu em <b>{fmt(nat_wins)}</b>"
        + (f"; sem vencedor claro em <b>{fmt(indef)}</b>" if indef else "")
        + ". Isso é calculado a partir da tabela acima, não é uma regra fixa — "
        "rode de novo e confira se mudou.</li>"
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>JIT vs Native Image — Benchmark</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>JIT Compilation vs GraalVM Native Image — Benchmark</h1>
  <p>Gerado em {now} · Spring Boot 3.3 · Java 21{scen_txt}</p>
</header>
<nav>
  <a href="#warmup">Warmup</a>
  <a href="#metricas">Métricas</a>
  {nav}
</nav>
<main>
{incomplete_banner}
  <div class="cards">
    <div class="card">
      <h4>Amostras JVM</h4>
      <div class="val">{total_jvm:,}</div>
      <div class="sub">cold start + warmup + steady state</div>
    </div>
    <div class="card">
      <h4>Amostras Native</h4>
      <div class="val">{total_nat:,}</div>
      <div class="sub">consistente desde o início</div>
    </div>
    <div class="card">
      <h4>TPS JVM</h4>
      <div class="val">{jvm_tps}</div>
      <div class="sub">req/s (todo o período)</div>
    </div>
    <div class="card">
      <h4>TPS Native</h4>
      <div class="val">{nat_tps}</div>
      <div class="sub">req/s (todo o período)</div>
    </div>
    <div class="card hl">
      <h4>Startup</h4>
      <div class="val" style="font-size:1rem;color:#43A047">{startup_val}</div>
      <div class="sub">{startup_sub}</div>
    </div>
    <div class="card">
      <h4>Heap JVM</h4>
      <div class="val" style="font-size:1rem">{jvm_h_b}→{jvm_h_a} MB</div>
      <div class="sub">antes → após o teste</div>
    </div>
    <div class="card">
      <h4>Heap Native</h4>
      <div class="val" style="font-size:1rem">{nat_h_b}→{nat_h_a} MB</div>
      <div class="sub">antes → após o teste</div>
    </div>
  </div>

  <!-- WARMUP CURVE -->
  <div class="section" id="warmup">
    <h2>Curva de Warmup — Primes/500 (CPU-bound)</h2>
    <p class="section-sub">Latência média por janelas de 5s. Curva completa: cold start → warmup → steady state.</p>
    <div class="ww">
      <div class="wc">
        <h4>🔵 HotSpot JIT</h4>
        {svg_jvm}
        <div style="font-size:.8rem;color:#616161">Cold start: <b>{jvm_first}ms</b> → Final: <b>{jvm_last}ms</b></div>
        <div style="font-size:.78rem;color:#9E9E9E;margin-top:5px;line-height:1.5">
          Queda nos primeiros ~30s = JIT compilando hot spots (tiers 1→4).
          Oscilações posteriores = pausas do G1GC e recompilações pontuais do C2.
        </div>
      </div>
      <div class="wc">
        <h4>🟢 GraalVM Native</h4>
        {svg_nat}
        <div style="font-size:.8rem;color:#616161">Cold start: <b>{nat_first}ms</b> → Final: <b>{nat_last}ms</b></div>
        <div style="font-size:.78rem;color:#9E9E9E;margin-top:5px;line-height:1.5">
          Sem JIT — binário já está compilado. Linha plana desde o primeiro request.
          Vantagem máxima em ambientes com cold starts frequentes (Kubernetes, serverless).
        </div>
      </div>
    </div>
  </div>

  <!-- MÉTRICAS PROMETHEUS -->
  <div class="section" id="metricas">
    <h2>Métricas do Processo — via Prometheus</h2>
    <p class="section-sub">Heap e CPU coletados a cada 15s durante o teste.</p>
    <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:14px">
      <div style="flex:1;min-width:200px">
        <div style="font-size:.8rem;font-weight:bold;color:#1E88E5;margin-bottom:4px">🔵 JVM — Heap (MB)</div>
        {jvm_heap_svg}
      </div>
      <div style="flex:1;min-width:200px">
        <div style="font-size:.8rem;font-weight:bold;color:#43A047;margin-bottom:4px">🟢 Native — Heap (MB)</div>
        {nat_heap_svg}
      </div>
      <div style="flex:1;min-width:200px">
        <div style="font-size:.8rem;font-weight:bold;color:#1E88E5;margin-bottom:4px">🔵 JVM — CPU%</div>
        {jvm_cpu_svg}
      </div>
      <div style="flex:1;min-width:200px">
        <div style="font-size:.8rem;font-weight:bold;color:#43A047;margin-bottom:4px">🟢 Native — CPU%</div>
        {nat_cpu_svg}
      </div>
    </div>
    <div class="warn-box">
      <b>Limites de container:</b> JVM = {JVM_MEM_MB} MB / {JVM_CPUS} CPUs · Native = {NATIVE_MEM_MB} MB / {NATIVE_CPUS} CPUs.
      (lidos das env vars JVM_MEM_MB/JVM_CPUS/NATIVE_MEM_MB/NATIVE_CPUS — mantenha sincronizadas
      com mem_limit/cpus do docker-compose.yml)
      O JVM precisa de mais recursos por causa do JIT compiler, Code Cache e G1GC concorrente.
      O Native Image entrega throughput comparável com menos recursos reservados.
    </div>
  </div>

  {ep_sections}

  <div class="note">
    <h3>📌 Como ler estes resultados</h3>
    <ul>
      <li><b>Verde = melhor, vermelho = pior</b> em cada linha. Para latência: menor é melhor. Para throughput: maior é melhor.</li>
      <li><b>Curva completa.</b> Os dados incluem cold start, warmup e steady state. O JVM começa lento e melhora — isso é intencional e honesto.</li>
      <li><b>p99</b> é o número mais importante: 99% dos requests foram mais rápidos que esse valor. É o que o usuário sente nos momentos de pico.</li>
      {conclusion_bullet}
      <li><b>Heurística geral (não medida neste teste, é regra de bolso da literatura):</b> pods de vida curta, serverless, scale-out frequente tendem a favorecer Native Image pelo startup; pods de longa duração tendem a favorecer JVM pelas otimizações do C2 acumuladas ao longo do tempo. Trate como ponto de partida para investigar, não como conclusão deste benchmark.</li>
    </ul>
  </div>

</main>
<footer>JIT Compilation Benchmark · Diego Brandão · 2026 · Apache JMeter 5.6 · Prometheus + Micrometer</footer>
</body>
</html>"""

if __name__=="__main__":
    if not os.path.exists(RAW_CSV):
        print(f"ERRO: {RAW_CSV} não encontrado"); sys.exit(1)
    rows=load_csv(RAW_CSV)
    if not rows:
        print("ERRO: CSV vazio"); sys.exit(1)
    mem_b=load_json(MEM_BEFORE)
    mem_a=load_json(MEM_AFTER)
    tl=load_jsonl(METRICS_TL)
    html=build_html(rows,mem_b,mem_a,tl)
    with open(OUT_HTML,"w",encoding="utf-8") as f:
        f.write(html)
    print(f"Relatório gerado: {OUT_HTML}")
