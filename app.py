import os
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort, jsonify, render_template_string
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, TemplateMessage, ButtonsTemplate, PostbackAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, PostbackEvent

app = Flask(__name__)

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
USER_ID = os.environ.get("LINE_USER_ID")  # 給 /remind、/missed 網址用
DASHBOARD_URL = "https://pillbot-9snv.onrender.com/dashboard"

records = []  # 服藥紀錄（Demo 用，暫存在記憶體，重新部署會清空）

# 示範病患資料（過去 30 天）。「王阿公」會加上即時紀錄，Demo 時會動
DEMO_PATIENTS = [
    {"name": "王阿公", "taken": 16, "missed": 2, "live": True},
    {"name": "陳阿嬤", "taken": 29, "missed": 1},
    {"name": "李先生", "taken": 18, "missed": 12},
    {"name": "林奶奶", "taken": 22, "missed": 8},
]

HELP_TEXT = (
    "💊 智慧藥盒小幫手\n"
    "你可以傳：\n"
    "・模擬提醒：收到服藥提醒\n"
    "・模擬漏服：家屬收到漏服警告\n"
    "・今日狀態：今天的服藥紀錄\n"
    "・遵從率：目前的服藥遵從率\n"
    "・後台：藥局後台網址"
)


def now_str():
    tw = timezone(timedelta(hours=8))  # 台灣時間
    return datetime.now(tw).strftime("%m/%d %H:%M")


def push(messages):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=USER_ID, messages=messages)
        )


def reply(event, messages):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message_with_http_info(
            ReplyMessageRequest(reply_token=event.reply_token, messages=messages)
        )


def build_reminder():
    return TemplateMessage(
        alt_text="該吃藥了",
        template=ButtonsTemplate(
            title="💊 該吃藥了",
            text="08:00 降血壓藥，吃了請按「已吃」",
            actions=[
                PostbackAction(label="已吃", data="taken"),
                PostbackAction(label="稍後提醒", data="snooze"),
            ],
        ),
    )


def get_rows():
    rows = []
    for p in DEMO_PATIENTS:
        taken, missed = p["taken"], p["missed"]
        if p.get("live"):  # 加上 LINE 上即時按的紀錄
            taken += sum(1 for r in records if r["status"] == "taken")
            missed += sum(1 for r in records if r["status"] == "missed")
        rate = round(taken / (taken + missed) * 100)
        rows.append({"name": p["name"], "taken": taken, "missed": missed, "rate": rate})
    return rows


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ---- 網址觸發（備用）----
@app.route("/remind")
def remind():
    push([build_reminder()])
    return "reminder sent"


@app.route("/missed")
def missed():
    records.append({"time": now_str(), "status": "missed"})
    push([TextMessage(text="⚠️ 提醒：阿公 08:00 的降血壓藥尚未服用，已超過 30 分鐘，請聯絡確認。")])
    return "missed alert sent"


@app.route("/log")
def log():
    return jsonify(records)


# ---- 藥局後台 ----
DASHBOARD_HTML = """
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>藥局後台</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body{font-family:sans-serif;max-width:800px;margin:24px auto;padding:0 16px}
  table{width:100%;border-collapse:collapse;margin-bottom:24px}
  th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}
  .high{color:#fff;background:#d33;padding:2px 10px;border-radius:10px}
  .ok{color:#fff;background:#2a9d57;padding:2px 10px;border-radius:10px}
</style>
</head>
<body>
<h1>💊 藥局後台：病患服藥總覽</h1>
<table>
  <tr><th>病患</th><th>已服</th><th>漏服</th><th>遵從率</th><th>風險</th></tr>
  {% for p in rows %}
  <tr>
    <td>{{ p.name }}</td><td>{{ p.taken }}</td><td>{{ p.missed }}</td><td>{{ p.rate }}%</td>
    <td>{% if p.rate < 80 %}<span class="high">高風險</span>{% else %}<span class="ok">穩定</span>{% endif %}</td>
  </tr>
  {% endfor %}
</table>
<canvas id="c" height="120"></canvas>
<script>
  const rows = {{ rows|tojson }};
  new Chart(document.getElementById('c'), {
    type: 'bar',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{ label: '遵從率 (%)', data: rows.map(r => r.rate) }]
    },
    options: { scales: { y: { min: 0, max: 100 } } }
  });
</script>
</body>
</html>
"""


@app.route("/dashboard")
def dashboard():
    return render_template_string(DASHBOARD_HTML, rows=get_rows())


# ---- LINE 事件 ----
@handler.add(PostbackEvent)
def handle_postback(event):
    if event.postback.data == "taken":
        records.append({"time": now_str(), "status": "taken"})
        text = "✅ 已記錄，今天的藥吃了，真棒！"
    else:
        records.append({"time": now_str(), "status": "snoozed"})
        text = "好的，10 分鐘後再提醒您。"
    reply(event, [TextMessage(text=text)])


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()

    if text == "模擬提醒":
        reply(event, [build_reminder()])

    elif text == "模擬漏服":
        records.append({"time": now_str(), "status": "missed"})
        reply(event, [TextMessage(
            text="⚠️ 【家屬通知】阿公 08:00 的降血壓藥尚未服用，已超過 30 分鐘，請聯絡確認。"
        )])

    elif text == "今日狀態":
        today = now_str()[:5]  # 例如 11/27
        todays = [r for r in records if r["time"].startswith(today)]
        taken = sum(1 for r in todays if r["status"] == "taken")
        missed_n = sum(1 for r in todays if r["status"] == "missed")
        snoozed = sum(1 for r in todays if r["status"] == "snoozed")
        reply(event, [TextMessage(
            text=f"📅 今日服藥狀態\n✅ 已服：{taken} 次\n⚠️ 漏服：{missed_n} 次\n⏰ 稍後提醒：{snoozed} 次"
        )])

    elif text in ("遵從率", "本週遵從率"):
        p = get_rows()[0]
        risk = "高風險 🔴" if p["rate"] < 80 else "穩定 🟢"
        reply(event, [TextMessage(
            text=f"📊 {p['name']} 服藥遵從率\n"
                 f"已服 {p['taken']} 次、漏服 {p['missed']} 次\n"
                 f"遵從率：{p['rate']}%（{risk}）"
        )])

    elif text == "後台":
        reply(event, [TextMessage(text=f"🏥 藥局後台：\n{DASHBOARD_URL}")])

    else:
        reply(event, [TextMessage(text=HELP_TEXT)])


if __name__ == "__main__":
    app.run(port=5000)
