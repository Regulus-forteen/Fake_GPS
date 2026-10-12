from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.clock import Clock
from kivy.metrics import dp
import socket

from jnius import autoclass
from android import mActivity


def get_local_ip():
    """获取手机在局域网中的 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


class ReceiverUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', padding=dp(16),
                         spacing=dp(12), **kwargs)

        self.add_widget(Label(
            text="[b]GPS 接收器[/b]", markup=True,
            size_hint_y=None, height=dp(40), font_size=dp(22)
        ))

        self.ip_label = Label(
            text=f"本机 IP: {get_local_ip()}",
            size_hint_y=None, height=dp(30), font_size=dp(18)
        )
        self.add_widget(self.ip_label)

        # 端口配置
        row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        row.add_widget(Label(text="监听端口", size_hint_x=0.4))
        self.port_input = TextInput(text="8888", multiline=False,
                                    input_filter='int', size_hint_x=0.6)
        row.add_widget(self.port_input)
        self.add_widget(row)

        # 状态显示
        self.status_label = Label(
            text="未启动", size_hint_y=None, height=dp(60),
            font_size=dp(16)
        )
        self.add_widget(self.status_label)

        # 启动/停止按钮
        self.btn = Button(text="启动服务", size_hint_y=None, height=dp(60),
                          font_size=dp(20))
        self.btn.bind(on_press=self.toggle_service)
        self.add_widget(self.btn)

        self.add_widget(Label(
            text="[size=12]使用前请在开发者选项中\n"
                 "将本应用设为模拟位置应用[/size]",
            markup=True, size_hint_y=None, height=dp(60),
            halign='center', valign='middle'
        ))

        self.service_running = False
        self.last_point = None
        Clock.schedule_interval(self.refresh_status, 1.0)

    def toggle_service(self, *args):
        from android import mActivity
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        Intent = autoclass('android.content.Intent')
        PythonService = autoclass('org.kivy.android.PythonService')

        if not self.service_running:
            try:
                port = int(self.port_input.text)
            except ValueError:
                self.status_label.text = "端口必须为数字"
                return

            service = autoclass('com.example.gpsreceiver.ServiceReceiver')
            mActivity.startService(
                Intent(mActivity, service).putExtra("port", port)
            )
            self.service_running = True
            self.btn.text = "停止服务"
            self.status_label.text = f"服务运行中，监听 {port}"
        else:
            service = autoclass('com.example.gpsreceiver.ServiceReceiver')
            mActivity.stopService(Intent(mActivity, service))
            self.service_running = False
            self.btn.text = "启动服务"
            self.status_label.text = "已停止"

    def refresh_status(self, dt):
        # 可以在这里通过共享文件或广播读取服务状态
        pass


class ReceiverApp(App):
    def build(self):
        self.title = "GPS 接收器"
        return ReceiverUI()


if __name__ == "__main__":
    ReceiverApp().run()