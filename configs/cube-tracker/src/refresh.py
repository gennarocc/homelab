#!/usr/bin/env python3
"""Sidecar that lets the dashboard's Refresh button run today's snapshot now.

The page is a static file served by nginx, so it has nothing to ask. This is
the smallest thing that can answer: one endpoint that creates a Job from the
existing CronJob, exactly as `kubectl create job --from=cronjob` would, using
the pod's own ServiceAccount.

It deliberately takes no parameters -- there is nothing to inject. The only
thing a caller can do is ask for the job that the CronJob would run anyway.

Stdlib only, same as the rest of this project.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = "https://kubernetes.default.svc"
CRONJOB = os.environ.get("CRONJOB", "cube-tracker")
PORT = int(os.environ.get("PORT", "8090"))
MIN_INTERVAL = int(os.environ.get("MIN_INTERVAL", "60"))   # seconds between runs
LABEL = "cube-tracker-trigger=manual"
KEEP = 2                                                    # finished jobs to retain

_last_start = 0.0


def _ns():
    with open(os.path.join(SA, "namespace")) as f:
        return f.read().strip()


def _token():
    with open(os.path.join(SA, "token")) as f:   # re-read: it is rotated
        return f.read().strip()


_ctx = ssl.create_default_context(cafile=os.path.join(SA, "ca.crt"))


def k8s(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + _token(),
                 "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=_ctx, timeout=30) as r:
        return json.load(r) if r.status != 204 else {}


def manual_jobs(ns):
    got = k8s("GET", f"/apis/batch/v1/namespaces/{ns}/jobs?labelSelector={LABEL}")
    return sorted(got.get("items", []),
                  key=lambda j: j["metadata"].get("creationTimestamp", ""))


def state(ns):
    jobs = manual_jobs(ns)
    if not jobs:
        return {"state": "idle"}
    st = jobs[-1].get("status", {})
    name = jobs[-1]["metadata"]["name"]
    if st.get("active"):
        return {"state": "running", "job": name}
    if st.get("succeeded"):
        return {"state": "done", "job": name}
    if st.get("failed"):
        return {"state": "failed", "job": name}
    return {"state": "running", "job": name}   # created, not yet scheduled


def start(ns):
    """Copy the CronJob's jobTemplate into a one-off Job, same as kubectl --from."""
    cj = k8s("GET", f"/apis/batch/v1/namespaces/{ns}/cronjobs/{CRONJOB}")
    spec = cj["spec"]["jobTemplate"]["spec"]
    spec["ttlSecondsAfterFinished"] = 900
    job = {"apiVersion": "batch/v1", "kind": "Job",
           "metadata": {"name": f"{CRONJOB}-manual-{int(time.time())}",
                        "labels": {"cube-tracker-trigger": "manual",
                                   "app": CRONJOB}},
           "spec": spec}
    created = k8s("POST", f"/apis/batch/v1/namespaces/{ns}/jobs", job)

    for old in manual_jobs(ns)[:-KEEP]:
        try:
            k8s("DELETE", f"/apis/batch/v1/namespaces/{ns}/jobs/"
                          f"{old['metadata']['name']}?propagationPolicy=Background")
        except Exception as exc:
            print(f"warn: could not reap {old['metadata']['name']}: {exc}", file=sys.stderr)
    return created["metadata"]["name"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") != "/api/refresh":
            return self._send(404, {"error": "not found"})
        try:
            self._send(200, state(_ns()))
        except Exception as exc:
            self._send(502, {"state": "failed", "error": str(exc)})

    def do_POST(self):
        global _last_start
        if self.path.rstrip("/") != "/api/refresh":
            return self._send(404, {"error": "not found"})
        try:
            ns = _ns()
            if state(ns)["state"] == "running":
                return self._send(409, {"error": "a refresh is already running"})
            wait = MIN_INTERVAL - (time.time() - _last_start)
            if wait > 0:
                return self._send(429, {"error": f"just ran; try again in {int(wait)}s"})
            name = start(ns)
            _last_start = time.time()
            self._send(202, {"state": "running", "job": name})
        except urllib.error.HTTPError as exc:
            self._send(502, {"error": f"kubernetes said {exc.code}: {exc.reason}"})
        except Exception as exc:
            self._send(502, {"error": str(exc)})

    def log_message(self, fmt, *args):
        sys.stderr.write("refresh: " + fmt % args + "\n")


if __name__ == "__main__":
    print(f"refresh sidecar on :{PORT}, cronjob={CRONJOB}, min interval {MIN_INTERVAL}s",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
