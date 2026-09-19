import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";

const BASE = __ENV.K6_URL || "http://127.0.0.1:8410";

export const options = {
  scenarios: {
    eval_200: {
      executor: "constant-arrival-rate",
      rate: 200,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 25,
      maxVUs: 60,
      exec: "evaluate",
      tags: { load: "200rps" },
    },
    eval_500: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 60,
      maxVUs: 150,
      exec: "evaluate",
      startTime: "25s",
      tags: { load: "500rps" },
    },
    eval_1000: {
      executor: "constant-arrival-rate",
      rate: 1000,
      timeUnit: "1s",
      duration: "15s",
      preAllocatedVUs: 120,
      maxVUs: 300,
      exec: "evaluate",
      startTime: "50s",
      tags: { load: "1000rps" },
    },
    withdrawal: {
      executor: "shared-iterations",
      vus: 8,
      iterations: 8,
      exec: "revoke",
      startTime: "68s",
    },
  },
  thresholds: {
    "http_req_duration{load:200rps}": ["p(95)<12"],
    "http_req_duration{load:500rps}": ["p(95)<12"],
    "http_req_duration{load:1000rps}": ["p(95)<12"],
    "http_req_failed": ["rate<0.01"],
  },
};

function assetFor(vu, iter) {
  return `bench-asset-${(vu + iter) % 8}`;
}

export function evaluate() {
  const payload = JSON.stringify({
    request_id: `k6-${exec.scenario.iterationInTest}-${exec.vu.idInTest}`,
    asset_id: assetFor(exec.vu.idInTest, exec.scenario.iterationInTest),
    tenant_id: "tenant-bench",
    action: "infer",
    auth_scopes: ["infer.basic"],
    prompt_text: "render a harbour at dusk",
  });
  const res = http.post(`${BASE}/evaluate`, payload, {
    headers: { "Content-Type": "application/json" },
    tags: { name: "evaluate" },
  });
  check(res, {
    "status 200": (r) => r.status === 200,
    "state allowed": (r) => {
      try {
        return r.json("state") === "Allowed";
      } catch {
        return false;
      }
    },
  });
}

export function revoke() {
  const target = `bench-asset-${exec.scenario.iterationInTest % 8}`;
  const res = http.post(
    `${BASE}/revoke`,
    JSON.stringify({ asset_id: target }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "revoke" } }
  );
  check(res, {
    "status 200": (r) => r.status === 200,
    "blocked immediately": (r) => {
      try {
        return r.json("blocked") === true;
      } catch {
        return false;
      }
    },
    "revoke-to-block < 50ms": (r) => {
      try {
        return r.json("revoke_to_block_ms") < 50;
      } catch {
        return false;
      }
    },
  });
}
