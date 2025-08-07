import sys
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import pytz

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QDialog, QFormLayout
)
from PyQt5.QtCore import QTimer

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mplfinance.original_flavor import candlestick_ohlc
import matplotlib.dates as mdates
from matplotlib.dates import DateFormatter

# Timeframe mapping
TIMEFRAME_MAP = {
    1: mt5.TIMEFRAME_M1,
    5: mt5.TIMEFRAME_M5,
    60: mt5.TIMEFRAME_H1,
    240: mt5.TIMEFRAME_H4,
    1440: mt5.TIMEFRAME_D1
}


class IndicatorDialog(QDialog):
    def __init__(self, parent=None, ind_type='None', period=20, bb_dev=2.0):
        super().__init__(parent)
        self.setWindowTitle('Indicator Settings')

        self.ind_type = ind_type
        self.period = period
        self.bb_dev = bb_dev

        layout = QFormLayout(self)

        self.type_cb = QComboBox()
        self.type_cb.addItems(['None', 'SMA', 'EMA', 'Bollinger'])
        self.type_cb.setCurrentText(self.ind_type)
        layout.addRow('Type:', self.type_cb)

        self.period_spin = QSpinBox()
        self.period_spin.setRange(1, 200)
        self.period_spin.setValue(self.period)
        layout.addRow('Period:', self.period_spin)

        self.bb_dev_spin = QDoubleSpinBox()
        self.bb_dev_spin.setRange(0.1, 5.0)
        self.bb_dev_spin.setSingleStep(0.1)
        self.bb_dev_spin.setValue(self.bb_dev)
        layout.addRow('BB Dev:', self.bb_dev_spin)

        btn_apply = QPushButton('Apply')
        btn_ok = QPushButton('OK')
        btn_apply.clicked.connect(self.apply)
        btn_ok.clicked.connect(self.accept)
        layout.addRow(btn_apply, btn_ok)

    def apply(self):
        self.ind_type = self.type_cb.currentText()
        self.period = self.period_spin.value()
        self.bb_dev = self.bb_dev_spin.value()

        parent = self.parent()
        if parent:
            parent.ind_type = self.ind_type
            parent.ind_period = self.period
            parent.ind_bb_dev = self.bb_dev
            parent.plot_full()

    def accept(self):
        self.apply()
        super().accept()


class TradeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('MT5 Trade')
        self.resize(450, 300)
        self.setStyleSheet('font-size:8pt;')

        if not mt5.initialize():
            raise RuntimeError('MT5 initialization failed')

        self.symbols = ['XAUUSDs', 'USDJPY']
        self.df_plot = pd.DataFrame()
        self.bar_width = None
        self.last_ask = self.last_bid = None
        self.bar_count = 50
        self.ind_type = 'None'
        self.ind_period = 20
        self.ind_bb_dev = 2.0
        self.selected_interval = 1

        self.init_ui()
        self.start_update()

    def init_ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        ml = QVBoxLayout(w)

        # Top panel
        r1 = QHBoxLayout()
        r1.addWidget(QLabel('Symbol:'))
        self.symbol_cb = QComboBox()
        self.symbol_cb.addItems(self.symbols)
        r1.addWidget(self.symbol_cb)
        self.ask_lbl = QLabel('Ask: -')
        self.bid_lbl = QLabel('Bid: -')
        r1.addWidget(self.ask_lbl)
        r1.addWidget(self.bid_lbl)
        r1.addWidget(QLabel('Bars:'))
        self.bar_spin = QSpinBox()
        self.bar_spin.setRange(1, 1440)
        self.bar_spin.setValue(self.bar_count)
        r1.addWidget(self.bar_spin)
        r1.addWidget(QLabel('Indicator:'))
        self.ind_btn = QPushButton('Indicator...')
        self.ind_btn.clicked.connect(self.open_ind_dialog)
        r1.addWidget(self.ind_btn)
        ml.addLayout(r1)

        # Bottom panel
        r2 = QHBoxLayout()
        r2.addWidget(QLabel('Lot:'))
        self.lot_spin = QDoubleSpinBox()
        self.lot_spin.setRange(0.01, 1000)
        self.lot_spin.setDecimals(2)
        self.lot_spin.setSingleStep(0.01)
        self.lot_spin.setValue(0.01)
        r2.addWidget(self.lot_spin)
        self.buy_btn = QPushButton('Buy')
        self.sell_btn = QPushButton('Sell')
        self.close_btn = QPushButton('Close')
        r2.addWidget(self.buy_btn)
        r2.addWidget(self.sell_btn)
        r2.addWidget(self.close_btn)
        self.pl_lbl = QLabel('P/L: 0.0')
        r2.addWidget(self.pl_lbl)
        ml.addLayout(r2)

        # Chart
        self.fig = Figure(figsize=(4.5, 3))
        self.canvas = FigureCanvas(self.fig)
        ml.addWidget(self.canvas)

        # Timeframe buttons
        tf_row = QHBoxLayout()
        tf_row.addWidget(QLabel('Timeframe:'))
        self.interval_buttons = {}
        for k in TIMEFRAME_MAP:
            label = f"{k}m" if k < 60 else (f"{k//60}H" if k < 1440 else "1d")
            b = QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _, x=k: self.select_interval(x))
            self.interval_buttons[k] = b
            tf_row.addWidget(b)
        self.interval_buttons[self.selected_interval].setChecked(True)
        ml.addLayout(tf_row)

        self.ax = self.fig.add_subplot(111)
        self.ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
        self.ax.yaxis.set_label_position('right')
        self.ax.yaxis.tick_right()

        # Connections
        self.buy_btn.clicked.connect(lambda: self.place_order(mt5.ORDER_TYPE_BUY))
        self.sell_btn.clicked.connect(lambda: self.place_order(mt5.ORDER_TYPE_SELL))
        self.close_btn.clicked.connect(self.close_all_positions)

    def select_interval(self, interval):
        self.selected_interval = interval
        for k, b in self.interval_buttons.items():
            b.setChecked(k == interval)

        if interval < 60:
            fmt = DateFormatter('%H:%M')
        elif interval < 1440:
            fmt = DateFormatter('%H')
        else:
            fmt = DateFormatter('%Y-%m-%d')
        self.ax.xaxis.set_major_formatter(fmt)
        self.bar_width = None

    def open_ind_dialog(self):
        IndicatorDialog(self, self.ind_type, self.ind_period, self.ind_bb_dev).exec_()

    def start_update(self):
        t = QTimer(self)
        t.timeout.connect(self.update_data)
        t.start(1000)

    def update_data(self):
        sym = self.symbol_cb.currentText()
        tf = TIMEFRAME_MAP[self.selected_interval]

        tick = mt5.symbol_info_tick(sym)
        if tick:
            self.last_ask, self.last_bid = tick.ask, tick.bid
            self.ask_lbl.setText(f'Ask: {self.last_ask}')
            self.bid_lbl.setText(f'Bid: {self.last_bid}')

        now = datetime.now(pytz.utc).astimezone(pytz.timezone('Asia/Tokyo'))
        bars_today = now.hour * 60 + now.minute + 1
        fc = max(self.bar_spin.value(), bars_today)

        rates = mt5.copy_rates_from_pos(sym, tf, 0, fc)
        if rates is None or len(rates) == 0:
            return

        df = pd.DataFrame(rates)[['time', 'open', 'high', 'low', 'close']]
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df['time'] = df['time'].dt.tz_convert('Asia/Tokyo').dt.tz_localize(None)
        df['time'] = df['time'].apply(mdates.date2num)
        self.df_plot = df

        if self.bar_width is None and len(df) >= 2:
            t0, t1 = df['time'].iloc[-2], df['time'].iloc[-1]
            self.bar_width = (t1 - t0) * 0.8

        self.plot_full()
        self.update_pl(sym)

    def plot_full(self):
        if self.df_plot.empty or self.bar_width is None:
            return

        d = self.df_plot.tail(self.bar_spin.value()).copy()
        self.ax.clear()

        candlestick_ohlc(self.ax, d.values, width=self.bar_width,
                         colorup='silver', colordown='gray')

        if self.ind_type == 'SMA':
            d['ind'] = d['close'].rolling(self.ind_period).mean()
            self.ax.plot(d['time'], d['ind'], linewidth=1)
        elif self.ind_type == 'EMA':
            d['ind'] = d['close'].ewm(span=self.ind_period, adjust=False).mean()
            self.ax.plot(d['time'], d['ind'], linewidth=1)
        elif self.ind_type == 'Bollinger':
            m = d['close'].rolling(self.ind_period).mean()
            s = d['close'].rolling(self.ind_period).std()
            self.ax.plot(d['time'], m + self.ind_bb_dev * s, linewidth=1)
            self.ax.plot(d['time'], m, linewidth=1)
            self.ax.plot(d['time'], m - self.ind_bb_dev * s, linewidth=1)

        if self.last_ask is not None:
            self.ax.axhline(self.last_ask, linestyle='--', linewidth=0.5)
        if self.last_bid is not None:
            self.ax.axhline(self.last_bid, linestyle='-.', linewidth=0.5)

        self.ax.xaxis_date()
        self.ax.set_xlim(d['time'].min(), d['time'].max() + self.bar_width * 10)
        self.ax.grid(True)
        self.canvas.draw()

    def place_order(self, ot):
        sym = self.symbol_cb.currentText()
        vol = float(self.lot_spin.value())
        pr = self.last_ask if ot == mt5.ORDER_TYPE_BUY else self.last_bid
        mt5.order_send({
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': sym,
            'volume': vol,
            'type': ot,
            'price': pr,
            'deviation': 10,
            'magic': 234000,
            'comment': 'python_mt5_trade',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC
        })

    def close_all_positions(self):
        for pos in mt5.positions_get() or []:
            mt5.order_close(
                pos.ticket, pos.volume,
                self.last_bid if pos.type == mt5.ORDER_TYPE_BUY else self.last_ask,
                10
            )
        self.update_pl(self.symbol_cb.currentText())

    def update_pl(self, sym):
        total = sum(p.profit for p in mt5.positions_get(symbol=sym) or [])
        self.pl_lbl.setText(f'P/L: {total:.2f}')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = TradeWindow()
    win.show()
    sys.exit(app.exec_())
