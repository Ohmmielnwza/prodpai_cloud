// k6 run alb_load_test.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom Metrics
const errorRate = new Rate('http_errors');
const backendDuration = new Trend('backend_req_duration');

export const options = {
  scenarios: {
    asg_scale_out: {
      executor: 'ramping-vus',
      startVUs: 5,
      stages: [
        { duration: '1m', target: 20 },  // อุ่นเครื่อง (Warm-up)
        { duration: '3m', target: 60 },  // เพิ่มโหลดเพื่อดัน CPU ให้แตะ Threshold (> 50-60%)
        { duration: '2m', target: 100 }, // พีคโหลดเพื่อดูการตอบสนองเมื่อ ASG สปอว์นเครื่องใหม่
        { duration: '1m', target: 0 },   // Cool-down ลดโหลดกลับลงมา
      ],
    },
  },
  thresholds: {
    // กำหนดเกณฑ์คุณภาพ: Error ต้องต่ำกว่า 1% และ 95% ของ Request ต้องตอบกลับเร็วกว่า 800ms
    http_errors: ['rate<0.01'],
    http_req_duration: ['p(95)<800'],
  },
};

export default function () {
  const TARGET_URL = 'http://prod-pai-env-lb-1228542498.us-east-1.elb.amazonaws.com/';

  const res = http.get(TARGET_URL, {
    timeout: '5s',
    headers: {
      'User-Agent': 'k6-alb-load-test',
      'Accept': 'text/html,application/json',
    },
  });

  const isSuccess = check(res, {
    'status is 200': (r) => r.status === 200,
  });

  errorRate.add(!isSuccess);
  backendDuration.add(res.timings.duration);

  // sleep สั้นๆ จำลองพฤติกรรม User จริง และป้องกันไม่ให้อัตราคำขอต่อวินาทีชน WAF เร็วเกินไป
  sleep(0.1);
}