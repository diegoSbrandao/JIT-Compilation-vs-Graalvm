package com.benchmark.app;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;
import java.io.*;
import java.lang.management.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;

@RestController
@RequestMapping("/api")
public class BenchmarkController {

    // Injeta o valor real da propriedade — lê do application.properties ou da env var VIRTUAL_THREADS
    @Value("${spring.threads.virtual.enabled:false}")
    private boolean virtualThreadsEnabled;

    // Nome da aplicação — identifica a variante nos logs e no /api/info
    @Value("${spring.application.name:unknown}")
    private String appName;

    // ── CPU-bound ──────────────────────────────────────────────────────────
    @GetMapping("/primes/{n}")
    public Map<String, Object> primes(@PathVariable int n) {
        long start = System.nanoTime();
        List<Integer> primes = new ArrayList<>();
        int candidate = 2;
        while (primes.size() < n) {
            if (isPrime(candidate)) primes.add(candidate);
            candidate++;
        }
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("count", primes.size());
        r.put("largest", primes.get(primes.size() - 1));
        r.put("elapsed_ms", elapsedMs);
        return r;
    }

    // ── Memory / GC pressure ───────────────────────────────────────────────
    @GetMapping("/memory/{size}")
    public Map<String, Object> memory(@PathVariable int size) {
        long start = System.nanoTime();
        List<String> items = new ArrayList<>(size);
        for (int i = 0; i < size; i++)
            items.add("item-" + i + "-" + UUID.randomUUID());
        long count = items.stream().filter(s -> s.contains("a")).count();
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("generated", size);
        r.put("matched", count);
        r.put("elapsed_ms", elapsedMs);
        return r;
    }

    // ── Fibonacci recursivo ────────────────────────────────────────────────
    @GetMapping("/fibonacci/{n}")
    public Map<String, Object> fibonacci(@PathVariable int n) {
        if (n > 40) n = 40;
        long start = System.nanoTime();
        long result = fib(n);
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("n", n);
        r.put("result", result);
        r.put("elapsed_ms", elapsedMs);
        return r;
    }

    // ── Ping — latência base ───────────────────────────────────────────────
    @GetMapping("/ping")
    public Map<String, Object> ping() {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("status", "ok");
        r.put("timestamp", System.currentTimeMillis());
        return r;
    }

    // ── Serialização / reflexão ────────────────────────────────────────────
    @GetMapping("/serialize/{n}")
    public Map<String, Object> serialize(@PathVariable int n) {
        if (n > 200) n = 200;
        long start = System.nanoTime();
        List<Map<String, Object>> objects = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            Map<String, Object> obj = new LinkedHashMap<>();
            obj.put("id", UUID.randomUUID().toString());
            obj.put("index", i);
            obj.put("name", "item-" + i);
            obj.put("active", i % 2 == 0);
            obj.put("score", Math.random() * 100);
            objects.add(obj);
        }
        long sum = objects.stream()
            .filter(o -> (boolean) o.get("active"))
            .mapToLong(o -> (int) o.get("index"))
            .sum();
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("objects_created", n);
        r.put("active_sum", sum);
        r.put("elapsed_ms", elapsedMs);
        return r;
    }

    // ── Sorting ────────────────────────────────────────────────────────────
    @GetMapping("/sort/{n}")
    public Map<String, Object> sort(@PathVariable int n) {
        if (n > 50000) n = 50000;
        long start = System.nanoTime();
        Random rnd = new Random(42);
        int[] arr = new int[n];
        for (int i = 0; i < n; i++) arr[i] = rnd.nextInt(1_000_000);
        Arrays.sort(arr);
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("n", n);
        r.put("min", arr[0]);
        r.put("max", arr[n - 1]);
        r.put("elapsed_ms", elapsedMs);
        return r;
    }

    // ── VIRTUAL THREADS — I/O concorrente ─────────────────────────────────
    // Simula N chamadas I/O paralelas (ex: consultas a APIs externas, banco)
    // Com Virtual Threads, cada chamada recebe sua própria thread leve
    // sem bloquear plataform threads do SO.
    // Este endpoint demonstra o caso de uso principal do Project Loom.
    @GetMapping("/concurrent/{n}")
    public Map<String, Object> concurrent(@PathVariable int n) throws Exception {
        if (n > 100) n = 100;
        long start = System.nanoTime();

        // ExecutorService com Virtual Threads (Java 21)
        try (ExecutorService vte = Executors.newVirtualThreadPerTaskExecutor()) {
            List<Future<Long>> futures = new ArrayList<>(n);
            for (int i = 0; i < n; i++) {
                final int delay = 10 + (i % 5) * 5; // 10-30ms simulando latência I/O
                futures.add(vte.submit(() -> {
                    Thread.sleep(delay);  // simula I/O blocking (banco, API externa)
                    return (long) delay;
                }));
            }
            long totalSimulatedIo = 0;
            for (Future<Long> f : futures)
                totalSimulatedIo += f.get();

            long elapsedMs = (System.nanoTime() - start) / 1_000_000;
            boolean isVirtual = Thread.currentThread().isVirtual();

            Map<String, Object> r = new LinkedHashMap<>();
            r.put("concurrent_tasks", n);
            r.put("total_simulated_io_ms", totalSimulatedIo);
            r.put("actual_elapsed_ms", elapsedMs);
            r.put("virtual_threads_active", isVirtual);
            // Se virtual threads estão ativos, elapsed deve ser ~30ms (paralelo)
            // Se fossem threads normais de plataforma, seria ~n*delay (sequencial)
            r.put("parallelism_factor",
                  String.format("%.1f×", (double) totalSimulatedIo / elapsedMs));
            return r;
        }
    }

    // ── MÉTRICAS DO SISTEMA — CPU e memória reais ──────────────────────────
    // Lê /proc/stat e /proc/self/status para obter dados reais do processo,
    // não apenas o heap Java reportado pelo Runtime.
    @GetMapping("/metrics")
    public Map<String, Object> metrics() {
        Runtime rt = Runtime.getRuntime();
        Map<String, Object> r = new LinkedHashMap<>();

        // ── Heap Java (via Runtime) ────────────────────────────────────────
        long heapUsed  = rt.totalMemory() - rt.freeMemory();
        long heapTotal = rt.totalMemory();
        long heapMax   = rt.maxMemory();
        r.put("heap_used_mb",  heapUsed  / 1024 / 1024);
        r.put("heap_total_mb", heapTotal / 1024 / 1024);
        r.put("heap_max_mb",   heapMax   / 1024 / 1024);

        // ── Memória do processo via /proc/self/status (Linux) ─────────────
        // VmRSS = Resident Set Size = memória física real do processo
        // Inclui heap + JIT + Code Cache + threads + SO libs — o número real
        long rssKb = readProcValue("/proc/self/status", "VmRSS:");
        long vmPeakKb = readProcValue("/proc/self/status", "VmPeak:");
        if (rssKb > 0) {
            r.put("rss_mb",     rssKb    / 1024);
            r.put("vm_peak_mb", vmPeakKb / 1024);
        }

        // ── Memória do container via cgroup ───────────────────────────────
        // Lê o limite e uso real do container Docker
        long cgroupUsage = readCgroupMemory(
            "/sys/fs/cgroup/memory.current",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes");
        long cgroupLimit = readCgroupMemory(
            "/sys/fs/cgroup/memory.max",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes");
        if (cgroupUsage > 0) {
            r.put("container_used_mb",  cgroupUsage / 1024 / 1024);
            r.put("container_limit_mb", cgroupLimit > 0 ? cgroupLimit / 1024 / 1024 : "unlimited");
        }

        // ── CPU via OperatingSystemMXBean ─────────────────────────────────
        // processCpuLoad: % de CPU usada por este processo (0.0 a 1.0)
        // systemCpuLoad: % de CPU usada pelo sistema inteiro
        OperatingSystemMXBean os =
            (OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();
        r.put("available_processors", os.getAvailableProcessors());
        r.put("system_load_avg", os.getSystemLoadAverage());

        // Tenta usar com.sun.management para % de CPU mais precisa
        try {
            com.sun.management.OperatingSystemMXBean sunOs =
                (com.sun.management.OperatingSystemMXBean) os;
            double procCpu = sunOs.getProcessCpuLoad() * 100;
            double sysCpu  = sunOs.getCpuLoad() * 100;
            r.put("process_cpu_pct", procCpu >= 0 ? String.format("%.1f", procCpu) : "n/a");
            r.put("system_cpu_pct",  sysCpu  >= 0 ? String.format("%.1f", sysCpu)  : "n/a");
        } catch (Exception e) {
            r.put("process_cpu_pct", "n/a");
        }

        // ── JIT / Compilação ──────────────────────────────────────────────
        CompilationMXBean comp = ManagementFactory.getCompilationMXBean();
        if (comp != null && comp.isCompilationTimeMonitoringSupported()) {
            r.put("jit_compiler",      comp.getName());
            r.put("jit_time_ms",       comp.getTotalCompilationTime());
        }

        // ── GC ────────────────────────────────────────────────────────────
        long gcCount = 0, gcTime = 0;
        for (GarbageCollectorMXBean gc : ManagementFactory.getGarbageCollectorMXBeans()) {
            gcCount += Math.max(gc.getCollectionCount(), 0);
            gcTime  += Math.max(gc.getCollectionTime(),  0);
        }
        r.put("gc_collections", gcCount);
        r.put("gc_time_ms",     gcTime);

        // ── Threads ───────────────────────────────────────────────────────
        ThreadMXBean threads = ManagementFactory.getThreadMXBean();
        r.put("thread_count",      threads.getThreadCount());
        r.put("thread_peak",       threads.getPeakThreadCount());
        r.put("virtual_threads_enabled", virtualThreadsEnabled);
        r.put("app_name",    appName);
        r.put("jvm_name",    System.getProperty("java.vm.name"));
        r.put("jvm_version", System.getProperty("java.version"));
        return r;
    }

    // ── Info básica ────────────────────────────────────────────────────────
    @GetMapping("/info")
    public Map<String, Object> info() {
        Runtime rt = Runtime.getRuntime();
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("app_name",       appName);
        r.put("jvm_name",       System.getProperty("java.vm.name"));
        r.put("jvm_version",    System.getProperty("java.version"));
        r.put("vendor",         System.getProperty("java.vendor"));
        r.put("max_memory_mb",   rt.maxMemory()   / 1024 / 1024);
        r.put("total_memory_mb", rt.totalMemory() / 1024 / 1024);
        r.put("free_memory_mb",  rt.freeMemory()  / 1024 / 1024);
        r.put("used_memory_mb",  (rt.totalMemory() - rt.freeMemory()) / 1024 / 1024);
        r.put("processors",     rt.availableProcessors());
        // Valor real lido do application.properties via @Value — não hardcoded
        r.put("virtual_threads", virtualThreadsEnabled ? "enabled" : "disabled");
        return r;
    }

    // ── helpers ────────────────────────────────────────────────────────────
    private boolean isPrime(int n) {
        if (n < 2) return false;
        if (n == 2) return true;
        if (n % 2 == 0) return false;
        for (int i = 3; i * i <= n; i += 2)
            if (n % i == 0) return false;
        return true;
    }

    private long fib(int n) {
        if (n <= 1) return n;
        return fib(n - 1) + fib(n - 2);
    }

    // Lê um valor numérico de /proc/self/status
    private long readProcValue(String file, String key) {
        try (BufferedReader br = new BufferedReader(new FileReader(file))) {
            String line;
            while ((line = br.readLine()) != null) {
                if (line.startsWith(key)) {
                    String[] parts = line.trim().split("\\s+");
                    if (parts.length >= 2) return Long.parseLong(parts[1]);
                }
            }
        } catch (Exception ignored) {}
        return -1;
    }

    // Lê uso/limite de memória do cgroup (v2 ou v1)
    private long readCgroupMemory(String pathV2, String pathV1) {
        for (String path : new String[]{pathV2, pathV1}) {
            try (BufferedReader br = new BufferedReader(new FileReader(path))) {
                String val = br.readLine().trim();
                if (!val.equals("max")) return Long.parseLong(val);
            } catch (Exception ignored) {}
        }
        return -1;
    }
}
