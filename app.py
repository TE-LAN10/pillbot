import os
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort, jsonify
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
USER_ID = os.environ.get("LINE_USER_ID")  # Demo：長輩和家屬都用這個帳號

records = []  # 服藥紀錄（Demo 用，暫存在記憶體，重新部署會清空）


def now_str():
    tw = timezone(timedelta(hours=8))  # 台灣時間
    return datetime.now(tw).strftime("%m/%d %H:%M")


def push(messages):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=USER_ID, messages=messages)
        )


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# 模擬「到服藥時間」：傳一則帶按鈕的提醒給長輩
@app.route("/remind")
def remind():
    msg = TemplateMessage(
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
    push([msg])
    return "reminder sent"


# 模擬「藥盒偵測到漏服」：通知家屬
@app.route("/missed")
def missed():
    records.append({"time": now_str(), "status": "missed"})
    push([TextMessage(text="⚠️ 提醒：阿公 08:00 的降血壓藥尚未服用，已超過 30 分鐘，請聯絡確認。")])
    return "missed alert sent"


# 看目前的服藥紀錄（之後藥局後台會用這份資料）
@app.route("/log")
def log():
    return jsonify(records)


# 長輩按下按鈕
@handler.add(PostbackEvent)
def handle_postback(event):
    if event.postback.data == "taken":
        records.append({"time": now_str(), "status": "taken"})
        reply = "✅ 已記錄，今天的藥吃了，真棒！"
    else:
        records.append({"time": now_str(), "status": "snoozed"})
        reply = "好的，10 分鐘後再提醒您。"
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message_with_http_info(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
        )


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=f"你說了：{event.message.text}")],
            )
        )


if __name__ == "__main__":
    app.run(port=5000)
