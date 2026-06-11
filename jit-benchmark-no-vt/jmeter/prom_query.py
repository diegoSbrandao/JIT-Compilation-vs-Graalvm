#!/usr/bin/env python3
"""
Script auxiliar para consultar o Prometheus sem problemas de escape de shell.
Uso: python3 prom_query.py <host> <port> <metric_query>
"""
import sys
import urllib.request
import urllib.parse
import json

host  = sys.argv[1]
port  = sys.argv[2]
query = sys.argv[3]

url = f"http://{host}:{port}/api/v1/query?query={urllib.parse.quote(query)}"
try:
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read())
    results = data.get("data", {}).get("result", [])
    if results:
        print(results[0].get("value", [None, None])[1])
    else:
        print("n/a")
except Exception:
    print("n/a")
