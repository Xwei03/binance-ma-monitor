import os
import requests

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK")

def send_feishu(msg):
    payload = {"msg_type": "text", "content": {"text": msg}}
    resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
    print(f"请求状态码: {resp.status_code}")
    print(f"返回结果: {resp.text}")

if __name__ == "__main__":
    send_feishu("🚨 六线粘合警报！飞书测试成功！")
