import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# 金鑰從「環境變數」讀取，不要直接寫在程式裡
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])


# LINE 會把使用者訊息送到這個網址（/callback）
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]  # 用來驗證訊息真的是 LINE 送的
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# 收到「文字訊息」時，執行下面這個函式
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,  # 回覆用的憑證，每則訊息都不同
                messages=[TextMessage(text=f"你說了：{event.message.text}")],
            )
        )


if __name__ == "__main__":
    app.run(port=5000)
