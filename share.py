from pyngrok import ngrok
import time
import subprocess
import os
import sys


def main():
    print("正在启动内网穿透服务，为您生成公网链接...")
    print("注意: 需要后台已启动 `python backend/main.py`！\n")
    try:
        # 打开ngrok隧道映射本地8000端口
        public_url = ngrok.connect(8000).public_url
        print("==" * 30)
        print("🎉 创建成功！请将以下网址发给其他人，或用手机直接打开：")
        print(f"\n👉 {public_url} 👈\n")
        print("其他人无需本文件夹，只要打开该链接即可使用实时估值系统！")
        print("按 Ctrl+C 关闭公网分享。")
        print("==" * 30)

        # 保持控制台运行
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n分享已关闭。")
        ngrok.kill()
        sys.exit(0)
    except Exception as e:
        error_msg = str(e)
        if "ERR_NGROK_4018" in error_msg:
            print("\n❌ 启动穿透失败：需要配置 ngrok authtoken。")
            print("1. 请前往 https://dashboard.ngrok.com/signup 注册并登录")
            print(
                "2. 在 https://dashboard.ngrok.com/get-started/your-authtoken 获取你的 Authtoken"
            )
            token = input(
                "\n请在此处粘贴你的 Authtoken 并按回车（只需输入一次即可永久生效）: "
            ).strip()
            if token:
                ngrok.set_auth_token(token)
                print("✅ Authtoken 设置成功！请重新运行 `python share.py`。")
            else:
                print("未输入 Authtoken，已退出。")
        else:
            print(f"\n发生错误: {e}")
            print("如果提示找不到依赖，请确保已运行：pip install pyngrok")


if __name__ == "__main__":
    main()
