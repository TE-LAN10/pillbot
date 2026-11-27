import os
import re
import random
import threading
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
USER_ID = os.environ.get("LINE_USER_ID")
DASHBOARD_URL = "https://pillbot-9snv.onrender.com/dashboard"

# ---- Demo 資料（暫存在記憶體，重新部署或休眠後會清空）----
records = []  # 服藥紀錄
meds = []     # 藥單
refill = {}   # 補藥通知：藥名 -> {"time", "left"}

DEMO_PATIENTS = [
    {"name": "王阿公", "taken": 16, "missed": 2, "live": True},
    {"name": "陳阿嬤", "taken": 29, "missed": 1},
    {"name": "李先生", "taken": 18, "missed": 12},
    {"name": "林奶奶", "taken": 22, "missed": 8},
]

SAMPLE_MEDS = [
    {"name": "降血壓藥", "time": "08:00", "dose": "1顆 飯後",
     "stock": 7, "init": 7},
    {"name": "維他命D", "time": "12:00", "dose": "1顆 隨餐",
     "stock": 10, "init": 10},
    {"name": "降血糖藥", "time": "20:00", "dose": "1顆 飯前",
     "stock": 10, "init": 10},
]

TIPS = [
    "💡 吃藥請配溫開水，避免用茶或咖啡送服。",
    "💡 漏服時不要一次吃雙倍劑量，請詢問藥師。",
    "💡 藥快吃完前一週就要規劃回診或領藥。",
    "💡 藥盒要放在乾燥、避免陽光直射的地方。",
    "💡 換新藥或加新藥前，記得告訴醫師目前吃的所有藥。",
]

HELP_TEXT = "\n".join([
    "💊 智慧藥盒小幫手",
    "【設定藥單】",
    "・新增藥物 藥名 時間 劑量 庫存數量",
    "  例：新增藥物 降血壓藥 08:00 1顆飯後 庫存10",
    "・刪除藥物 藥名",
    "・載入範例：一次載入三筆範例藥單",
    "【功能】",
    "・今日藥單／庫存",
    "・模擬提醒／模擬漏服（可加藥名）",
    "・今日狀態／最近紀錄／遵從率",
    "・衛教／後台／重置示範",
])

NO_MEDS_TEXT = "\n".join([
    "目前還沒有藥單，請先新增，例如：",
    "新增藥物 降血壓藥 08:00 1顆飯後 庫存10",
    "（或傳「載入範例」）",
])

USAGE_TEXT = "\n".join([
    "格式：新增藥物 藥名 時間 劑量 庫存數量",
    "例：新增藥物 降血壓藥 08:00 1顆飯後 庫存10",
])


def now_str():
    tw = timezone(timedelta(hours=8))
    return datetime.now(tw).strftime("%m/%d %H:%M")


def push_to(user_id, messages):
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(to=user_id, messages=messages))


def reply(event, messages):
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages,
            )
        )


def say(event, text):
    reply(event, [TextMessage(text=text)])


# ---- 藥單工具 ----
def find_med(name):
    for m in meds:
        if m["name"] == name:
            return m
    return None


def sorted_meds():
    return sorted(meds, key=lambda m: m["time"])


def pick_med(name=""):
    if name:
        return find_med(name)
    if meds:
        return sorted_meds()[0]
    return None


def parse_add(text):
    parts = text.split()
    if len(parts) < 3:
        return None, USAGE_TEXT
    name = parts[1]
    t = re.fullmatch(r"(\d{1,2}):(\d{2})", parts[2])
    if not t or int(t.group(1)) > 23 or int(t.group(2)) > 59:
        return None, "時間請用 08:00 這種格式\n" + USAGE_TEXT
    time_str = f"{int(t.group(1)):02d}:{t.group(2)}"
    dose_parts = []
    stock = 10
    for p in parts[3:]:
        if p.startswith("庫存") and p[2:].isdigit():
            stock = int(p[2:])
        else:
            dose_parts.append(p)
    med = {
        "name": name,
        "time": time_str,
        "dose": " ".join(dose_parts),
        "stock": stock,
        "init": stock,
    }
    return med, None


def fmt_med_line(m):
    line = f"{m['time']} {m['name']}"
    if m["dose"]:
        line += f"（{m['dose']}）"
    return line


def fmt_stock_line(m):
    line = f"{m['name']}：剩 {m['stock']} 顆"
    if m["stock"] <= 5:
        line += " ⚠️ 請補藥"
    return line


def fmt_record_line(r):
    icon = {
        "taken": "✅ 已服",
        "missed": "⚠️ 漏服",
        "snoozed": "⏰ 稍後提醒",
    }
    return f"{r['time']} {icon[r['status']]} {r.get('med', '')}"


def build_reminder(med):
    info = f"{med['time']} {med['name']}"
    if med["dose"]:
        info += f"（{med['dose']}）"
    return TemplateMessage(
        alt_text="該吃藥了",
        template=ButtonsTemplate(
            title="💊 該吃藥了",
            text=info[:60],
            actions=[
                PostbackAction(label="已吃", data="taken:" + med["name"]),
                PostbackAction(label="稍後提醒", data="snooze:" + med["name"]),
            ],
        ),
    )


def missed_text(med):
    return (
        f"⚠️ 【家屬通知】阿公 {med['time']} 的{med['name']}"
        "尚未服用，已超過 30 分鐘，請聯絡確認。"
    )


def delayed_remind(user_id, med):
    try:
        push_to(user_id, [build_reminder(med)])
    except Exception as e:
        print("delayed remind failed:", e)


def count_status(status):
    return sum(1 for r in records if r["status"] == status)


def get_rows():
    rows = []
    for p in DEMO_PATIENTS:
        taken = p["taken"]
        missed = p["missed"]
        if p.get("live"):
            taken += count_status("taken")
            missed += count_status("missed")
        rate = round(taken / (taken + missed) * 100)
        rows.append({
            "name": p["name"],
            "taken": taken,
            "missed": missed,
            "rate": rate,
        })
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
    med = pick_med()
    if not med:
        return "no meds"
    push_to(USER_ID, [build_reminder(med)])
    return "reminder sent"


@app.route("/missed")
def missed():
    med = pick_med()
    if not med:
        return "no meds"
    records.append({"time": now_str(), "status": "missed", "med": med["name"]})
    push_to(USER_ID, [TextMessage(text=missed_text(med))])
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
  .btn{background:#06c755;color:#fff;padding:4px 12px;border-radius:6px;
       text-decoration:none;font-size:14px}
  .box{background:#fff4e5;border:1px solid #ffb84d;border-radius:8px;
       padding:12px 16px;margin-bottom:8px}
</style>
</head>
<body>
<h1>💊 藥局後台：病患服藥總覽</h1>

<h2>🔔 補藥通知</h2>
{% if refill %}
  {% for name, r in refill.items() %}
  <div class="box">{{ r.time }} 王阿公 {{ name }} 剩 {{ r.left }} 顆，請準備補藥</div>
  {% endfor %}
{% else %}
<p>目前沒有補藥需求</p>
{% endif %}

<h2>👥 病患總覽</h2>
<table>
  <tr>
    <th>病患</th><th>已服</th><th>漏服</th><th>遵從率</th><th>風險</th><th>操作</th>
  </tr>
  {% for p in rows %}
  <tr>
    <td>{{ p.name }}</td>
    <td>{{ p.taken }}</td>
    <td>{{ p.missed }}</td>
    <td>{{ p.rate }}%</td>
    <td>
      {% if p.rate < 80 %}<span class="high">高風險</span>
      {% else %}<span class="ok">穩定</span>{% endif %}
    </td>
    <td><a class="btn" href="/care?name={{ p.name|urlencode }}">發送關懷</a></td>
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
    return render_template_string(DASHBOARD_HTML, rows=get_rows(), refill=refill)


@app.route("/care")
def care():
    name = request.args.get("name", "您")
    text = (
        f"🏥 【藥局關懷】{name}您好，藥師注意到您最近有幾次漏服，"
        "需要協助調整提醒時間嗎？用藥上有任何問題都可以直接問我們。"
    )
    push_to(USER_ID, [TextMessage(text=text)])
    return "<script>alert('已發送關懷訊息');history.back()</script>"


# ---- LINE 事件 ----
@handler.add(PostbackEvent)
def handle_postback(event):
    action, _, name = event.postback.data.partition(":")
    med = find_med(name)

    if action == "taken":
        records.append({"time": now_str(), "status": "taken", "med": name})
        text = f"✅ 已記錄，{name}吃了，真棒！"
        if med:
            med["stock"] = max(0, med["stock"] - 1)
            if med["stock"] <= 5:
                refill[name] = {"time": now_str(), "left": med["stock"]}
                text += f"\n📦 {name}只剩 {med['stock']} 顆，已通知藥局準備補藥。"
        say(event, text)
    else:
        records.append({"time": now_str(), "status": "snoozed", "med": name})
        if med:
            timer = threading.Timer(
                30, delayed_remind, args=[event.source.user_id, med]
            )
            timer.start()
        say(event, "好的，30 秒後再提醒您（示範用，正式版約 10 分鐘）。")


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip().replace("：", ":")

    # ---- 設定藥單 ----
    if text.startswith("新增藥物"):
        med, err = parse_add(text)
        if err:
            say(event, err)
            return
        old = find_med(med["name"])
        if old:
            meds.remove(old)
        meds.append(med)
        msg = f"✅ 已新增：{fmt_med_line(med)}（庫存 {med['stock']} 顆）"
        msg += "\n傳「今日藥單」查看，或傳「模擬提醒」測試。"
        say(event, msg)

    elif text.startswith("刪除藥物"):
        name = text.replace("刪除藥物", "", 1).strip()
        med = find_med(name)
        if med:
            meds.remove(med)
            refill.pop(name, None)
            say(event, f"🗑️ 已刪除：{name}")
        else:
            say(event, f"找不到「{name}」，請傳「今日藥單」確認藥名。")

    elif text == "載入範例":
        meds.clear()
        refill.clear()
        for m in SAMPLE_MEDS:
            meds.append(dict(m))
        say(event, "✅ 已載入範例藥單（三筆），傳「今日藥單」查看。")

    # ---- 查詢 ----
    elif text == "今日藥單":
        if not meds:
            say(event, NO_MEDS_TEXT)
        else:
            lines = [fmt_med_line(m) for m in sorted_meds()]
            say(event, "📋 今日藥單\n" + "\n".join(lines))

    elif text == "庫存":
        if not meds:
            say(event, NO_MEDS_TEXT)
        else:
            lines = [fmt_stock_line(m) for m in sorted_meds()]
            say(event, "📦 藥物庫存\n" + "\n".join(lines))

    # ---- 模擬 ----
    elif text.startswith("模擬提醒"):
        med = pick_med(text.replace("模擬提醒", "", 1).strip())
        if med:
            reply(event, [build_reminder(med)])
        else:
            say(event, NO_MEDS_TEXT)

    elif text.startswith("模擬漏服"):
        med = pick_med(text.replace("模擬漏服", "", 1).strip())
        if med:
            records.append({
                "time": now_str(),
                "status": "missed",
                "med": med["name"],
            })
            say(event, missed_text(med))
        else:
            say(event, NO_MEDS_TEXT)

    # ---- 紀錄與分析 ----
    elif text == "今日狀態":
        today = now_str()[:5]
        todays = [r for r in records if r["time"].startswith(today)]
        taken = sum(1 for r in todays if r["status"] == "taken")
        missed_n = sum(1 for r in todays if r["status"] == "missed")
        snoozed = sum(1 for r in todays if r["status"] == "snoozed")
        msg = "\n".join([
            "📅 今日服藥狀態",
            f"✅ 已服：{taken} 次",
            f"⚠️ 漏服：{missed_n} 次",
            f"⏰ 稍後提醒：{snoozed} 次",
        ])
        say(event, msg)

    elif text == "最近紀錄":
        if not records:
            say(event, "目前還沒有紀錄")
        else:
            lines = [fmt_record_line(r) for r in records[-5:]]
            say(event, "🕘 最近紀錄\n" + "\n".join(lines))

    elif text in ("遵從率", "本週遵從率"):
        p = get_rows()[0]
        risk = "高風險 🔴" if p["rate"] < 80 else "穩定 🟢"
        msg = "\n".join([
            f"📊 {p['name']} 服藥遵從率",
            f"已服 {p['taken']} 次、漏服 {p['missed']} 次",
            f"遵從率：{p['rate']}%（{risk}）",
        ])
        say(event, msg)

    elif text == "衛教":
        say(event, random.choice(TIPS))

    elif text == "後台":
        say(event, f"🏥 藥局後台：\n{DASHBOARD_URL}")

    elif text == "重置示範":
        records.clear()
        refill.clear()
        for m in meds:
            m["stock"] = m["init"]
        say(event, "🔄 已清空紀錄並還原庫存（藥單保留）")

    else:
        say(event, HELP_TEXT)


if __name__ == "__main__":
    app.run(port=5000)
