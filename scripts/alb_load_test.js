import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  scenarios: {
    light_load: {
      executor: 'constant-vus',
      vus: 30,
      duration: '10m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const TARGET_URL = 'http://prod-pai-env-lb-1847368857.us-east-1.elb.amazonaws.com/';

  const res = http.get(TARGET_URL, {
    headers: {
      'User-Agent': 'k6-basic-load-test',
      'Connection': 'close', 
  }});

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(0.5);
}