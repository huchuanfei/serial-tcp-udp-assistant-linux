# -*- coding: utf-8 -*-
"""串口调试助手
基于 tkinter + pyserial，支持 HEX/ASCII 显示与发送模式（勾选选择）。
"""
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import serial
import serial.tools.list_ports

MAX_RECV = 4096


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex(text: str) -> bytes:
    """解析 HEX 文本为字节，支持 01 A2 3F / 0x01,0xA2 等写法"""
    tokens = re.findall(r"0?[0-9a-fA-F]{1,2}", text.replace("0x", "").replace("0X", ""))
    return bytes(int(t, 16) for t in tokens)


class SerialAssistantApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("串口调试助手")
        self.root.geometry("900x680")
        self.root.minsize(640, 480)

        self.ser = None
        self.running = False
        self.rx_thread = None
        self.rx_queue = queue.Queue()
        self.timer_send_job = None  # 定时发送 after 任务
        self.frame_gap_ms = 50      # 帧合并空闲超时(ms)，UI 可调

        self._build_ui()
        self._refresh_ports()
        self.root.after(50, self._poll_rx_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        # 顶部：串口参数配置
        top = ttk.LabelFrame(self.root, text="串口设置")
        top.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(top, text="端口:").grid(row=0, column=0, padx=4, pady=6, sticky="e")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=14,
                                       postcommand=self._refresh_ports)
        self.port_combo.grid(row=0, column=1, padx=2)
        ttk.Button(top, text="刷新", width=4, command=self._refresh_ports).grid(row=0, column=2, padx=2)

        ttk.Label(top, text="波特率:").grid(row=0, column=3, padx=4, sticky="e")
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(top, textvariable=self.baud_var, width=8, state="readonly",
                     values=["9600", "19200", "38400", "57600", "115200", "230400",
                             "460800", "921600"]).grid(row=0, column=4, padx=2)

        ttk.Label(top, text="数据位:").grid(row=0, column=5, padx=4, sticky="e")
        self.databits_var = tk.StringVar(value="8")
        ttk.Combobox(top, textvariable=self.databits_var, width=3, state="readonly",
                     values=["5", "6", "7", "8"]).grid(row=0, column=6, padx=2)

        ttk.Label(top, text="停止位:").grid(row=1, column=0, padx=4, pady=6, sticky="e")
        self.stopbits_var = tk.StringVar(value="1")
        ttk.Combobox(top, textvariable=self.stopbits_var, width=8, state="readonly",
                     values=["1", "1.5", "2"]).grid(row=1, column=1, padx=2, sticky="w")

        ttk.Label(top, text="校验位:").grid(row=1, column=3, padx=4, sticky="e")
        self.parity_var = tk.StringVar(value="无")
        ttk.Combobox(top, textvariable=self.parity_var, width=8, state="readonly",
                     values=["无", "奇校验", "偶校验"]).grid(row=1, column=4, padx=2, sticky="w")

        self.btn_open = ttk.Button(top, text="打开串口", command=self._open)
        self.btn_open.grid(row=1, column=5, padx=8)
        self.btn_close = ttk.Button(top, text="关闭串口", command=self._close, state="disabled")
        self.btn_close.grid(row=1, column=6, padx=4)

        self.status_var = tk.StringVar(value="状态: 未打开")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=7, rowspan=2,
                                                          padx=12, sticky="w")
        top.columnconfigure(7, weight=1)

        # 中部：接收区（尽可能大）—— 外层框架禁用 propagate 避免内容变化时引起整体抖动
        rx_frame = ttk.LabelFrame(self.root, text="接收区")
        rx_frame.pack(fill="both", expand=True, padx=8, pady=4)
        rx_frame.pack_propagate(False)

        opt_bar = ttk.Frame(rx_frame)
        opt_bar.pack(fill="x", padx=4, pady=2)

        ttk.Label(opt_bar, text="显示模式:").pack(side="left")
        self.rx_ascii_var = tk.BooleanVar(value=True)
        self.rx_hex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_bar, text="ASCII", variable=self.rx_ascii_var,
                        command=lambda: self._on_mode_change("rx", "ascii")).pack(side="left", padx=2)
        ttk.Checkbutton(opt_bar, text="HEX", variable=self.rx_hex_var,
                        command=lambda: self._on_mode_change("rx", "hex")).pack(side="left", padx=2)

        # 帧合并空闲超时: USB串口芯片会以几十ms的间隔批量上送数据，
        # 该时间内持续到达的数据合并为一帧。波特率低或芯片上送间隔大时调大。
        ttk.Label(opt_bar, text="帧超时(ms):").pack(side="left", padx=(12, 0))
        self.frame_gap_var = tk.StringVar(value=str(self.frame_gap_ms))
        self.frame_gap_var.trace_add("write", self._on_gap_change)
        ttk.Entry(opt_bar, textvariable=self.frame_gap_var, width=5).pack(side="left")

        self.btn_clear_rx = ttk.Button(opt_bar, text="清空接收区", command=self._clear_rx)
        self.btn_clear_rx.pack(side="right", padx=2)
        self.btn_save_rx = ttk.Button(opt_bar, text="保存日志", command=self._save_rx)
        self.btn_save_rx.pack(side="right", padx=2)

        # Text + 双滚动条放在独立容器，外层先横条(BOTTOM)再内容(TOP)，避免布局抖动
        rx_body = ttk.Frame(rx_frame)
        rx_body.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        rx_body.pack_propagate(False)

        # 横向滚动条 —— 放在底部
        rx_xscroll = ttk.Scrollbar(rx_body, orient="horizontal")
        rx_xscroll.pack(side="bottom", fill="x")

        # 竖滚条 + Text 容器
        rx_inner = ttk.Frame(rx_body)
        rx_inner.pack(side="top", fill="both", expand=True)

        self.rx_text = tk.Text(rx_inner, wrap="none", font=("Consolas", 11))
        self.rx_text.pack(side="left", fill="both", expand=True)
        rx_yscroll = ttk.Scrollbar(rx_inner, orient="vertical", command=self.rx_text.yview)
        rx_yscroll.pack(side="right", fill="y")
        self.rx_text.configure(yscrollcommand=rx_yscroll.set, xscrollcommand=rx_xscroll.set)
        rx_xscroll.configure(command=self.rx_text.xview)
        self.rx_text.configure(state="disabled")

        # 底部：发送区
        tx_frame = ttk.LabelFrame(self.root, text="发送区")
        tx_frame.pack(fill="x", padx=8, pady=(4, 8))

        tx_bar = ttk.Frame(tx_frame)
        tx_bar.pack(fill="x", padx=4, pady=2)

        ttk.Label(tx_bar, text="发送模式:").pack(side="left")
        self.tx_ascii_var = tk.BooleanVar(value=True)
        self.tx_hex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tx_bar, text="ASCII", variable=self.tx_ascii_var,
                        command=lambda: self._on_mode_change("tx", "ascii")).pack(side="left", padx=2)
        ttk.Checkbutton(tx_bar, text="HEX", variable=self.tx_hex_var,
                        command=lambda: self._on_mode_change("tx", "hex")).pack(side="left", padx=2)

        self.auto_reply = tk.BooleanVar(value=False)
        ttk.Checkbutton(tx_bar, text="收到数据自动回复相同内容", variable=self.auto_reply).pack(side="left", padx=12)

        # 定时发送
        self.timer_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tx_bar, text="定时发送", variable=self.timer_var,
                        command=self._on_timer_toggle).pack(side="left", padx=12)
        ttk.Label(tx_bar, text="间隔(ms):").pack(side="left")
        self.interval_var = tk.StringVar(value="1000")
        ttk.Entry(tx_bar, textvariable=self.interval_var, width=7).pack(side="left", padx=2)

        # 发送按钮放到这一行最右侧
        self.btn_send = ttk.Button(tx_bar, text="发送", command=self._send, width=10)
        self.btn_send.pack(side="right", padx=(0, 0))

        # 发送文本框 + 右侧竖滚条 + 底部横滚条，与接收区结构一致，左右 padding 对齐 (padx=4)
        tx_body = ttk.Frame(tx_frame)
        tx_body.pack(fill="x", padx=4, pady=(0, 4))

        # 先 pack 横滚条(底部)，再 pack 内容区(顶部)，避免布局抖动
        tx_xscroll = ttk.Scrollbar(tx_body, orient="horizontal")
        tx_xscroll.pack(side="bottom", fill="x")

        tx_inner = ttk.Frame(tx_body)
        tx_inner.pack(side="top", fill="both", expand=True)

        self.tx_text = tk.Text(tx_inner, height=4, wrap="none", font=("Consolas", 11))
        self.tx_text.pack(side="left", fill="both", expand=True)
        tx_yscroll = ttk.Scrollbar(tx_inner, orient="vertical", command=self.tx_text.yview)
        tx_yscroll.pack(side="right", fill="y")
        self.tx_text.configure(yscrollcommand=tx_yscroll.set, xscrollcommand=tx_xscroll.set)
        tx_xscroll.configure(command=self.tx_text.xview)

    def _on_mode_change(self, area: str, mode: str):
        """HEX/ASCII 两个勾选互斥"""
        if area == "rx":
            if mode == "hex":
                self.rx_ascii_var.set(not self.rx_hex_var.get())
            else:
                self.rx_hex_var.set(not self.rx_ascii_var.get())
        else:
            if mode == "hex":
                self.tx_ascii_var.set(not self.tx_hex_var.get())
            else:
                self.tx_hex_var.set(not self.tx_ascii_var.get())

    def _on_gap_change(self, *_):
        """帧超时输入变化 → 更新接收线程使用的毫秒值"""
        try:
            v = int(self.frame_gap_var.get())
        except ValueError:
            return
        self.frame_gap_ms = max(5, min(1000, v))

    # ---------------- 串口 ----------------
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo.configure(values=ports)
        if ports and not self.port_var.get():
            self.port_var.set(ports[-1])

    @staticmethod
    def _parity_map(name: str):
        return {"无": serial.PARITY_NONE, "奇校验": serial.PARITY_ODD, "偶校验": serial.PARITY_EVEN}[name]

    @staticmethod
    def _stopbits_map(name: str):
        return {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                "2": serial.STOPBITS_TWO}[name]

    def _open(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("错误", "请先选择串口端口")
            return
        try:
            baud = int(self.baud_var.get())
            databits = int(self.databits_var.get())
            self.ser = serial.Serial(
                port=port, baudrate=baud, bytesize=databits,
                stopbits=self._stopbits_map(self.stopbits_var.get()),
                parity=self._parity_map(self.parity_var.get()),
                timeout=0.2)
        except (ValueError, serial.SerialException) as e:
            messagebox.showerror("错误", f"打开串口失败:\n{e}")
            self.ser = None
            return

        self.running = True
        self.rx_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.rx_thread.start()

        self.btn_open.configure(state="disabled")
        self.btn_close.configure(state="normal")
        desc = f"{port} {baud}bps {self.databits_var.get()}-{self.parity_var.get()[0]}-{self.stopbits_var.get()}"
        self.status_var.set(f"状态: 已打开 {desc}")
        self._append_system(f"串口已打开: {desc}\n")

    def _close(self):
        self._stop_timer()
        self.running = False
        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=1.0)
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None
        self.btn_open.configure(state="normal")
        self.btn_close.configure(state="disabled")
        self.status_var.set("状态: 未打开")
        self._append_system("串口已关闭\n")

    def _recv_loop(self):
        while self.running and self.ser:
            try:
                # read(1) 等首字节，一到即返回；不能用 read(MAX_RECV)，
                # 否则其超时窗口会把相邻两帧的数据一次读走
                first = self.ser.read(1)
            except serial.SerialException:
                # 串口被拔出等异常 → 通知 UI
                if self.running:
                    self.running = False
                    self.rx_queue.put(("error", "串口读取异常，可能已被拔出\n"))
                break
            if not first:
                continue
            ts = datetime.now()
            data = first
            # 帧合并：USB串口芯片会以几十ms的间隔批量上送数据(尤其在低波特率时)，
            # 算法: 持续到达的数据(相邻间隔 < 帧超时)合并为同一帧，
            #       连续"帧超时"时间内无新数据才判定帧结束。帧大小上限 64KB。
            gap = max(5, self.frame_gap_ms) / 1000.0
            idle_deadline = time.monotonic() + gap
            while self.running and len(data) < 65536:
                time.sleep(0.005)
                try:
                    n = self.ser.in_waiting
                except serial.SerialException:
                    n = 0
                if n:
                    try:
                        data += self.ser.read(n)
                    except serial.SerialException:
                        break
                    idle_deadline = time.monotonic() + gap
                elif time.monotonic() >= idle_deadline:
                    break
            self.rx_queue.put(("data", data, ts))

    def _poll_rx_queue(self):
        try:
            while True:
                item = self.rx_queue.get_nowait()
                if item[0] == "data":
                    self._display_rx(item[1], item[2])
                elif item[0] == "error":
                    self._force_closed()
                    self._append_system(item[1])
        except queue.Empty:
            pass
        self.root.after(50, self._poll_rx_queue)

    def _force_closed(self):
        """串口异常断开后复位 UI（主线程调用）"""
        self._stop_timer()
        self.running = False
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None
        self.btn_open.configure(state="normal")
        self.btn_close.configure(state="disabled")
        self.status_var.set("状态: 未打开")

    def _display_rx(self, data: bytes, ts: datetime):
        # 固定格式: [hhmmss-字节数(6位)] （串口无来源地址）
        head = ts.strftime("[%H%M%S-") + f"{len(data):06d}] "

        if self.rx_hex_var.get():
            body = bytes_to_hex(data)
        else:
            body = data.decode("utf-8", errors="replace")

        self.rx_text.configure(state="normal")
        self.rx_text.insert("end", head + body + "\n")
        self.rx_text.see("end")
        self.rx_text.configure(state="disabled")

        if self.auto_reply.get():
            self._send_data(data)

    def _append_system(self, text: str):
        self.rx_text.configure(state="normal")
        self.rx_text.insert("end", f"--- {text}")
        self.rx_text.see("end")
        self.rx_text.configure(state="disabled")

    # ---------------- 发送 ----------------
    def _send(self):
        if not self.running or not self.ser:
            messagebox.showwarning("提示", "请先打开串口")
            return
        text = self.tx_text.get("1.0", "end").strip()
        if not text:
            return
        # 去掉回车/换行，仅发送可见字符
        text = text.replace("\r", "").replace("\n", "")
        if not text:
            return
        try:
            if self.tx_hex_var.get():
                data = parse_hex(text)
            else:
                data = text.encode("utf-8")
        except ValueError:
            messagebox.showerror("错误", "HEX 格式无效，请使用如 01 A2 3F 的格式")
            return
        if not data:
            messagebox.showerror("错误", "发送内容为空")
            return
        self._send_data(data)

    def _send_data(self, data: bytes):
        if not self.running or not self.ser:
            return
        try:
            self.ser.write(data)
        except serial.SerialException as e:
            messagebox.showerror("错误", f"发送失败:\n{e}")

    # ---------------- 定时发送 ----------------
    def _on_timer_toggle(self):
        if self.timer_var.get():
            if not self.running or not self.ser:
                messagebox.showwarning("提示", "定时发送需要先打开串口")
                self.timer_var.set(False)
                return
            try:
                ms = int(self.interval_var.get())
                if ms < 10:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "定时发送间隔无效（最小 10ms）")
                self.timer_var.set(False)
                return
            self._schedule_timer()
        else:
            self._stop_timer()

    def _schedule_timer(self):
        if not self.timer_var.get() or not self.running:
            self._stop_timer()
            return
        self._timer_once()
        try:
            ms = int(self.interval_var.get())
        except ValueError:
            self._stop_timer()
            return
        self.timer_send_job = self.root.after(ms, self._schedule_timer)

    def _timer_once(self):
        """定时发送一次当前发送区内容"""
        if not self.running or not self.ser:
            return
        text = self.tx_text.get("1.0", "end").strip()
        if not text:
            return
        text = text.replace("\r", "").replace("\n", "")
        if not text:
            return
        try:
            if self.tx_hex_var.get():
                data = parse_hex(text)
            else:
                data = text.encode("utf-8")
        except ValueError:
            return
        if data:
            try:
                self.ser.write(data)
            except serial.SerialException:
                self._stop_timer()

    def _stop_timer(self):
        if self.timer_send_job:
            self.root.after_cancel(self.timer_send_job)
            self.timer_send_job = None

    # ---------------- 其他 ----------------
    def _clear_rx(self):
        self.rx_text.configure(state="normal")
        self.rx_text.delete("1.0", "end")
        self.rx_text.configure(state="disabled")

    def _save_rx(self):
        from tkinter import filedialog
        content = self.rx_text.get("1.0", "end")
        if not content.strip():
            messagebox.showinfo("提示", "接收区为空")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".log", filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    def _on_close(self):
        self._close()
        self.root.destroy()


def main():
    root = tk.Tk()
    SerialAssistantApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
