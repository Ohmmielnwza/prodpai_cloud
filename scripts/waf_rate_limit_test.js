// k6 run waf_rate_limit_test.js
import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

// Custom Metrics แยกผลลัพธ์ระหว่างคำขอที่ผ่าน กับที่โดน WAF บล็อก
const count200 = new Counter('http_200_allowed');
const count403 = new Counter('waf_403_blocked');
const blockedPercentage = new Rate('waf_blocked_ratio');

export const options = {
  scenarios: {
    waf_flood_test: {
      executor: 'constant-arrival-rate',
      rate: 25,            // ส่งคงที่ 25 req/sec (~1,500 requests ใน 1 นาที)
      timeUnit: '1s',
      duration: '1m',      // ยิงต่อเนื่อง 1 นาที
      preAllocatedVUs: 30, // จัดสรร VUs ล่วงหน้าเพื่อยิงให้ได้ Rate สม่ำเสมอ
      maxVUs: 60,
    },
  },
};

export default function () {
  const TARGET_URL = 'http://prod-pai-env-lb-1228542498.us-east-1.elb.amazonaws.com/';

  const res = http.get(TARGET_URL, {
    timeout: '3s',
    headers: {
      'User-Agent': 'k6-waf-stress-tester',
    },
  });

  if (res.status === 200) {
    count200.add(1);
    blockedPercentage.add(false);
  } else if (res.status === 403) {
    count403.add(1);
    blockedPercentage.add(true);
  }

  check(res, {
    'request allowed (200)': (r) => r.status === 200,
    'blocked by WAF (403)': (r) => r.status === 403,
  });
}