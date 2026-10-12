import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const errors5xx = new Counter('http_5xx_errors');
const errorRate = new Rate('failure_rate');
const waitingTime = new Trend('waiting_time_ttfb');

export const options = {
  scenarios: {
    // 1. Stress Scenario: อัดโหลดอย่างหนักเพื่อหา Breaking Point
    stress_ramp: {
      executor: 'ramping-vus',
      startVUs: 10,
      stages: [
        { duration: '1m', target: 10 },   // Warm-up
        { duration: '1m', target: 30 },  // เกิน Capacity มาตรฐานของ 1-2 instance
        { duration: '5m', target: 30 },  // จุด Stress: ดัน CPU/Memory ให้ชน 100%
        { duration: '2m', target: 30 },  // ขยี้ระบบเพื่อดู ALB Queue & Dropped connections
        { duration: '2m', target: 0 },    // สังเกตการฟื้นตัว (Recovery phase)
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    // กำหนดว่า 90% ของ Request เวลารอรับไบต์แรก (TTFB) ไม่ควรเกิน 1.5 วินาที
    waiting_time_ttfb: ['p(90)<1500'],
  },
};

export default function () {
  const TARGET_URL = 'http://prod-pai-env-lb-1847368857.us-east-1.elb.amazonaws.com/';

  const res = http.get(TARGET_URL, {
    timeout: '6s',
    headers: { 
      'User-Agent': 'k6-stress-test-agent',
      'Connection': 'close'
    },
  });

  const isSuccess = check(res, {
    'is status 200': (r) => r.status === 200,
  });

  if (res.status >= 500) {
    errors5xx.add(1);
  }

  errorRate.add(!isSuccess);
  waitingTime.add(res.timings.waiting);

  sleep(0.05); // หน่วงเวลาสั้นมากเพื่อสร้าง Concurrency สูงสุด
}