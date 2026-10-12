# k6 run waf_test.js

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

// สร้าง Custom Metrics เพื่อนับ 200 OK vs 403 WAF Blocked โดยเฉพาะ
const successCount = new Counter('http_200_success');
const blockedCount = new Counter('waf_403_blocked');
const blockedRate  = new Rate('waf_blocked_rate');

export const options = {
  scenarios: {
    // กำหนดยิงแบบคงที่ต่อวินาที (Constant Arrival Rate)
    waf_trigger: {
      executor: 'constant-arrival-rate',
      rate: 20,            // 20 requests/sec (~1,200 requests/min ทะลุ Limit 300 แน่นอน)
      timeUnit: '1s',
      duration: '1m',      // รันต่อเนื่อง 1 นาที
      preAllocatedVUs: 20, // จำนวน Virtual Users ล่วงหน้า
      maxVUs: 50,
    },
  },
  thresholds: {
    // สามารถตั้งให้ Alert ได้เมื่อสัดส่วนที่โดน Block เกินเป้า
    'waf_blocked_rate': ['rate>0'], 
  },
};

export default function () {
  const url = 'http://prod-pai-env-lb-1228542498.us-east-1.elb.amazonaws.com/';
  const res = http.get(url, { timeout: '3s' });

  // ตรวจสอบสถานะ Response
  if (res.status === 200) {
    successCount.add(1);
    blockedRate.add(false);
  } else if (res.status === 403) {
    blockedCount.add(1);
    blockedRate.add(true);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'blocked by WAF (403)': (r) => r.status === 403,
  });
}