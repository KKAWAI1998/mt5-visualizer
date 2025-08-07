import sys
import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton
)
from PyQt5.QtCore import QTimer
import pyqtgraph as pg
from pyqtgraph import DateAxisItem
# mt5_in_dat.py（非公開ファイル）から以下の関数をインポート
from mt5_in_dat import get_latest_price, get_historical_prices  # 独自実装：MT5からデータ取得


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("r-monitor")  # ウィンドウタイトル設定
        self.resize(1000, 600)  # 初期サイズ設定

        # ---- 表示スケール（X: 時間軸、Y: 値幅） ----
        self.scale_x = 30 * 60  # X軸：過去30分（秒単位）
        self.scale_y_factor = 1.0  # Y軸の拡大縮小倍率

        # ---- メインレイアウト設定（左右分割） ----
        main_layout = QHBoxLayout(self)

        # ---- 左側：コントロールボタン ----
        control_layout = QVBoxLayout()
        self.btn_zoom_in = QPushButton("x 拡大")
        self.btn_zoom_out = QPushButton("x 縮小")
        self.btn_y_zoom_in = QPushButton("Y 拡大")
        self.btn_y_zoom_out = QPushButton("Y 縮小")

        # ボタンを縦に配置
        control_layout.addWidget(self.btn_zoom_in)
        control_layout.addWidget(self.btn_zoom_out)
        control_layout.addWidget(self.btn_y_zoom_in)
        control_layout.addWidget(self.btn_y_zoom_out)
        control_layout.addStretch()  # 下にスペース追加
        main_layout.addLayout(control_layout)

        # ---- 右側：グラフ描画領域 ----
        plot_layout = QVBoxLayout()
        axis = DateAxisItem(orientation='bottom')  # X軸を時刻表示に
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': axis})
        self.plot_widget.showAxis("right")  # Y軸の右側も表示
        self.plot_widget.getAxis("right").setStyle(showValues=True)
        self.curve = self.plot_widget.plot([], [], pen='w')  # 線グラフ（白）
        self.view_box = self.plot_widget.getPlotItem().getViewBox()
        plot_layout.addWidget(self.plot_widget)
        main_layout.addLayout(plot_layout)

        # ---- データ保持変数 ----
        self.times = []    # タイムスタンプ（Unix時間）
        self.prices = []   # 中値（(bid + ask)/2）

        # ---- 初期ヒストリーデータの読み込み ----
        self.load_history()

        # ---- リアルタイム更新用タイマー（100msごとに新データ追加） ----
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(100)

        # ---- 長押しズーム用タイマー（X軸/Y軸） ----
        self.timer_zoom_in = QTimer()
        self.timer_zoom_out = QTimer()
        self.timer_y_zoom_in = QTimer()
        self.timer_y_zoom_out = QTimer()
        self.timer_zoom_in.timeout.connect(self.zoom_in)
        self.timer_zoom_out.timeout.connect(self.zoom_out)
        self.timer_y_zoom_in.timeout.connect(self.y_zoom_in)
        self.timer_y_zoom_out.timeout.connect(self.y_zoom_out)
        self.zoom_interval_ms = 150  # 長押し時の繰り返し間隔

        # ---- ボタンイベント接続 ----
        self.btn_zoom_in.pressed.connect(
            lambda: self.timer_zoom_in.start(self.zoom_interval_ms))
        self.btn_zoom_in.released.connect(self.timer_zoom_in.stop)
        self.btn_zoom_out.pressed.connect(
            lambda: self.timer_zoom_out.start(self.zoom_interval_ms))
        self.btn_zoom_out.released.connect(self.timer_zoom_out.stop)
        self.btn_y_zoom_in.pressed.connect(
            lambda: self.timer_y_zoom_in.start(self.zoom_interval_ms))
        self.btn_y_zoom_in.released.connect(self.timer_y_zoom_in.stop)
        self.btn_y_zoom_out.pressed.connect(
            lambda: self.timer_y_zoom_out.start(self.zoom_interval_ms))
        self.btn_y_zoom_out.released.connect(self.timer_y_zoom_out.stop)

        # 単押し時のクリックにも反応
        self.btn_zoom_in.clicked.connect(self.zoom_in)
        self.btn_zoom_out.clicked.connect(self.zoom_out)
        self.btn_y_zoom_in.clicked.connect(self.y_zoom_in)
        self.btn_y_zoom_out.clicked.connect(self.y_zoom_out)

    def load_history(self):
        """
        過去30分の価格データを取得して初期表示用データに格納
        """
        end_dt = datetime.datetime.now()
        start_dt = end_dt - datetime.timedelta(minutes=30)
        hist = get_historical_prices(start_dt, end_dt)
        for ts, bid, ask in hist:
            timestamp = ts.timestamp() if isinstance(ts, datetime.datetime) else ts
            self.times.append(timestamp)
            self.prices.append((bid + ask) / 2)

        # 古い順にソート（安全処理）
        combined = sorted(zip(self.times, self.prices), key=lambda x: x[0])
        self.times, self.prices = zip(*combined) if combined else ([], [])
        self.times = list(self.times)
        self.prices = list(self.prices)

    def zoom_in(self):
        """X軸の表示範囲を狭める（拡大）"""
        self.scale_x = max(60, self.scale_x - 60)  # 最低1分
        self.update_view_range()

    def zoom_out(self):
        """X軸の表示範囲を広げる（縮小）"""
        self.scale_x += 60
        self.update_view_range()

    def y_zoom_in(self):
        """Y軸のスケールを拡大（値幅を狭く）"""
        self.scale_y_factor = max(0.1, self.scale_y_factor * 0.8)
        self.update_view_range()

    def y_zoom_out(self):
        """Y軸のスケールを縮小（値幅を広く）"""
        self.scale_y_factor = min(10.0, self.scale_y_factor * 1.2)
        self.update_view_range()

    def update_data(self):
        """
        最新の価格データを取得してリストに追加、
        古いデータは30分以上経過していたら削除
        """
        bid, ask = get_latest_price()
        mid = (bid + ask) / 2
        now = datetime.datetime.now().timestamp()
        self.times.append(now)
        self.prices.append(mid)

        # 30分より古いデータ削除
        cutoff = now - 30 * 60
        while self.times and self.times[0] < cutoff:
            self.times.pop(0)
            self.prices.pop(0)

        self.curve.setData(self.times, self.prices)  # グラフ描画更新
        self.update_view_range()

    def update_view_range(self):
        """現在のズーム倍率に応じて表示範囲（X, Y）を更新"""
        if not self.times or not self.prices:
            return

        now = datetime.datetime.now().timestamp()
        x_right = now + self.scale_x * 0.3  # 少し未来まで表示
        x_left = x_right - self.scale_x
        self.view_box.setXRange(x_left, x_right, padding=0)

        # Y軸の中心は最新価格
        mid_y = self.prices[-1]
        span = max(0.0001, max(self.prices[-100:]) - min(self.prices[-100:]))
        half_range = (span / 2) * self.scale_y_factor
        desired_min = mid_y - half_range
        desired_max = mid_y + half_range

        current_min, current_max = self.view_box.viewRange()[1]
        current_span = current_max - current_min
        if desired_min < current_min - 0.1 * current_span or desired_max > current_max + 0.1 * current_span:
            self.view_box.setYRange(desired_min, desired_max, padding=0)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
